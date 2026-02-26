import hashlib
import hmac
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from db import ensure_tables, enqueue_comment_tasks, insert_ig_event
from integrations.instagram import extract_comment_tasks


logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Instagram Webhook", version="0.1.0")


@app.on_event("startup")
async def on_startup() -> None:
    ensure_tables()
    logger.info("Instagram webhook service started")


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    expected_token = (os.getenv("META_VERIFY_TOKEN") or "").strip()
    if not expected_token:
        raise HTTPException(status_code=500, detail="META_VERIFY_TOKEN is not configured")

    if hub_mode != "subscribe" or hub_verify_token != expected_token:
        raise HTTPException(status_code=403, detail="Webhook verification failed")

    return PlainTextResponse(hub_challenge)


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
    tasks = extract_comment_tasks(payload)
    created_count = enqueue_comment_tasks(tasks, source_event_id=event_id)

    logger.info(
        "Webhook processed: event_id=%s object=%s tasks_seen=%s tasks_created=%s",
        event_id,
        payload.get("object"),
        len(tasks),
        created_count,
    )
    return JSONResponse(
        {
            "ok": True,
            "event_id": event_id,
            "comment_tasks_seen": len(tasks),
            "comment_tasks_created": created_count,
        }
    )


def _validate_signature(request: Request, raw_body: bytes) -> bool | None:
    app_secret = (os.getenv("META_APP_SECRET") or "").strip()
    if not app_secret:
        return None

    received = request.headers.get("x-hub-signature-256", "")
    if not received.startswith("sha256="):
        return False

    expected = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, f"sha256={expected}")


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
