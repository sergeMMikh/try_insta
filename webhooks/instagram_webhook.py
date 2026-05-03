import hashlib
import hmac
import json
import logging
import os
import threading
from contextlib import nullcontext
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from db import (
    ensure_tables,
    enqueue_comment_tasks,
    get_comment_reply_mode,
    insert_ig_event,
)
from integrations.instagram import extract_comment_tasks
from integrations.langfuse_support import (
    build_trace_context,
    get_langfuse_client,
    get_trace_url,
    propagate_langfuse_attributes,
    serialize_for_langfuse,
    update_observation,
)
from workers.comments_worker import CommentsWorker


logger = logging.getLogger(__name__)

load_dotenv(override=True)

app = FastAPI(title="Instagram Webhook", version="0.1.0")
_worker_thread: threading.Thread | None = None


@app.on_event("startup")
async def on_startup() -> None:
    _configure_worker_logging()
    ensure_tables()
    _maybe_start_comments_worker()
    app_secret = (os.getenv("META_APP_SECRET") or "").strip()
    env_reply_mode = (os.getenv("IG_REPLY_MODE") or "").strip().lower() or "<unset>"
    effective_reply_mode = get_comment_reply_mode()
    logger.warning(
        "Instagram webhook service started; META_VERIFY_TOKEN configured=%s; META_APP_SECRET configured=%s; META_APP_SECRET fingerprint=%s; IG_START_COMMENTS_WORKER=%s; IG_REPLY_MODE env=%s effective=%s",
        bool((os.getenv("META_VERIFY_TOKEN") or "").strip()),
        bool(app_secret),
        _secret_fingerprint(app_secret),
        _is_truthy_env("IG_START_COMMENTS_WORKER"),
        env_reply_mode,
        effective_reply_mode,
    )


@app.get("/")
@app.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_mode_alt: str = Query("", alias="hub_mode"),
    hub_verify_token_alt: str = Query("", alias="hub_verify_token"),
    hub_challenge_alt: str = Query("", alias="hub_challenge"),
) -> PlainTextResponse:
    # Meta usually sends dotted params (hub.mode), but occasionally integrations
    # pass underscore aliases. Accept both to avoid flaky verification failures.
    del request
    mode_value = hub_mode or hub_mode_alt
    verify_token_value = hub_verify_token or hub_verify_token_alt
    challenge_value = hub_challenge or hub_challenge_alt
    langfuse = get_langfuse_client()
    observation_cm = (
        langfuse.start_as_current_observation(
            as_type="tool",
            name="instagram.webhook.verify",
            input={
                "mode": mode_value,
                "challenge_length": len(challenge_value),
            },
            metadata={
                "provider": "instagram",
                "route": "/webhook",
            },
        )
        if langfuse is not None
        else nullcontext(None)
    )

    with observation_cm as observation:
        expected_token = (os.getenv("META_VERIFY_TOKEN") or "").strip()
        if not expected_token:
            logger.error("Webhook verification failed: META_VERIFY_TOKEN is not configured")
            update_observation(
                observation,
                level="ERROR",
                status_message="META_VERIFY_TOKEN is not configured",
                output={"accepted": False, "status_code": 500},
            )
            raise HTTPException(status_code=500, detail="META_VERIFY_TOKEN is not configured")

        if mode_value != "subscribe" or verify_token_value != expected_token:
            logger.warning(
                "Webhook verification rejected: mode=%r token_len=%s expected_token_len=%s challenge_len=%s",
                mode_value,
                len(verify_token_value),
                len(expected_token),
                len(challenge_value),
            )
            update_observation(
                observation,
                level="WARNING",
                status_message="Webhook verification rejected",
                output={"accepted": False, "status_code": 403},
            )
            raise HTTPException(status_code=403, detail="Webhook verification failed")

        logger.info(
            "Webhook verification accepted: mode=%r challenge_len=%s",
            mode_value,
            len(challenge_value),
        )
        update_observation(
            observation,
            output={"accepted": True, "challenge_length": len(challenge_value)},
        )
        return PlainTextResponse(challenge_value)


@app.post("/")
@app.post("/webhook")
async def receive_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty webhook body")

    signature_valid = _validate_signature(request, raw_body)
    if signature_valid is False:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be JSON object")

    event_id = insert_ig_event(
        payload=payload,
        headers=_extract_headers(request),
        signature_valid=signature_valid,
    )
    langfuse = get_langfuse_client()
    trace_context = build_trace_context(f"instagram-event:{event_id}")
    observation_cm = (
        langfuse.start_as_current_observation(
            as_type="tool",
            name="instagram.webhook.receive",
            trace_context=trace_context,
            input={
                "payload": serialize_for_langfuse(payload),
                "headers": serialize_for_langfuse(_extract_headers(request)),
            },
            metadata={
                "provider": "instagram",
                "event_id": str(event_id),
                "signature_valid": str(signature_valid),
            },
        )
        if langfuse is not None
        else nullcontext(None)
    )

    with observation_cm as observation:
        trace_url = (
            get_trace_url(getattr(observation, "trace_id", None))
            if observation is not None
            else None
        )
        if trace_url:
            logger.info("Langfuse webhook trace: event_id=%s url=%s", event_id, trace_url)

        with propagate_langfuse_attributes(
            session_id=f"instagram-event:{event_id}",
            metadata={
                "service": "webhook",
                "provider": "instagram",
                "event_id": event_id,
            },
            trace_name="instagram-webhook",
        ):
            tasks = extract_comment_tasks(payload)
            created_count = enqueue_comment_tasks(tasks, source_event_id=event_id)

            response_payload = {
                "ok": True,
                "event_id": event_id,
                "comment_tasks_seen": len(tasks),
                "comment_tasks_created": created_count,
            }
            update_observation(
                observation,
                output=serialize_for_langfuse(response_payload),
                metadata={
                    "object_type": str(payload.get("object") or ""),
                    "tasks_seen": str(len(tasks)),
                    "tasks_created": str(created_count),
                },
            )

            logger.info(
                "Webhook processed: event_id=%s object=%s tasks_seen=%s tasks_created=%s",
                event_id,
                payload.get("object"),
                len(tasks),
                created_count,
            )
            return JSONResponse(response_payload)


def _validate_signature(request: Request, raw_body: bytes) -> bool | None:
    app_secret = (os.getenv("META_APP_SECRET") or "").strip()
    if not app_secret:
        logger.warning(
            "Webhook signature validation skipped: META_APP_SECRET is not configured; body_len=%s",
            len(raw_body),
        )
        return None

    received = request.headers.get("x-hub-signature-256", "")
    if not received.startswith("sha256="):
        logger.warning(
            "Webhook signature missing or malformed: header_present=%s header_prefix=%r body_len=%s",
            bool(received),
            received[:12],
            len(raw_body),
        )
        return False

    expected = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    expected_full = f"sha256={expected}"
    matches = hmac.compare_digest(received, expected_full)
    if not matches:
        logger.warning(
            "Webhook signature mismatch: received_prefix=%s expected_prefix=%s body_len=%s ua=%r",
            _signature_prefix(received),
            _signature_prefix(expected_full),
            len(raw_body),
            request.headers.get("user-agent", ""),
        )
    else:
        logger.info(
            "Webhook signature accepted: body_len=%s ua=%r",
            len(raw_body),
            request.headers.get("user-agent", ""),
        )
    return matches


def _signature_prefix(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 20:
        return value
    return f"{value[:16]}...{value[-8:]}"


def _secret_fingerprint(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


def _extract_headers(request: Request) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for header_name in (
        "user-agent",
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-hub-signature-256",
        "content-type",
    ):
        header_value = request.headers.get(header_name)
        if header_value:
            result[header_name] = header_value
    return result


def _maybe_start_comments_worker() -> None:
    global _worker_thread

    if not _is_truthy_env("IG_START_COMMENTS_WORKER"):
        return
    if _worker_thread and _worker_thread.is_alive():
        return

    _worker_thread = threading.Thread(
        target=_run_comments_worker_forever,
        name="ig-comments-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Background comments worker thread started")


def _run_comments_worker_forever() -> None:
    try:
        CommentsWorker().run_forever()
    except Exception:
        logger.exception("Background comments worker stopped unexpectedly")


def _is_truthy_env(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _configure_worker_logging() -> None:
    worker_logger = logging.getLogger("workers.comments_worker")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")

    worker_logger.setLevel(logging.INFO)
    if uvicorn_error_logger.handlers:
        worker_logger.handlers = list(uvicorn_error_logger.handlers)
        worker_logger.propagate = False
