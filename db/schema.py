import logging
import os
from collections.abc import Iterable
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

from .engine import get_engine


logger = logging.getLogger(__name__)

load_dotenv()


def ensure_tables() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ig_event (
                    id BIGSERIAL PRIMARY KEY,
                    object_type VARCHAR(64),
                    entry_count INTEGER NOT NULL DEFAULT 0,
                    payload_json JSONB NOT NULL,
                    headers_json JSONB,
                    signature_valid BOOLEAN,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ig_comment_task (
                    id BIGSERIAL PRIMARY KEY,
                    comment_id VARCHAR(64) NOT NULL UNIQUE,
                    media_id VARCHAR(64),
                    parent_id VARCHAR(64),
                    commenter_username TEXT,
                    comment_text TEXT,
                    payload_json JSONB,
                    source_event_id BIGINT REFERENCES ig_event(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'todo',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    reply_mode_snapshot VARCHAR(16),
                    reply_text TEXT,
                    reply_comment_id VARCHAR(64),
                    processed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ig_comment_task_status_created_idx
                ON ig_comment_task (status, created_at)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    value_text TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO bot_settings (setting_key, value_text)
                VALUES ('comment_reply_mode', :default_mode)
                ON CONFLICT (setting_key) DO NOTHING
                """
            ),
            {"default_mode": _normalize_reply_mode(os.getenv("IG_REPLY_MODE", "draft"))},
        )


def insert_ig_event(
    payload: dict[str, Any],
    headers: dict[str, Any] | None = None,
    signature_valid: bool | None = None,
) -> int:
    engine = get_engine()
    entry = payload.get("entry")
    entry_count = len(entry) if isinstance(entry, list) else 0
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO ig_event (
                    object_type,
                    entry_count,
                    payload_json,
                    headers_json,
                    signature_valid
                ) VALUES (
                    :object_type,
                    :entry_count,
                    CAST(:payload_json AS JSONB),
                    CAST(:headers_json AS JSONB),
                    :signature_valid
                )
                RETURNING id
                """
            ),
            {
                "object_type": str(payload.get("object") or ""),
                "entry_count": entry_count,
                "payload_json": _to_json(payload),
                "headers_json": _to_json(headers or {}),
                "signature_valid": signature_valid,
            },
        ).mappings().one()
    return int(row["id"])


def enqueue_comment_tasks(
    tasks: Iterable[dict[str, Any]],
    source_event_id: int | None,
) -> int:
    engine = get_engine()
    inserted = 0
    with engine.begin() as conn:
        for task in tasks:
            comment_id = str(task.get("comment_id") or "").strip()
            if not comment_id:
                continue
            row = conn.execute(
                text(
                    """
                    INSERT INTO ig_comment_task (
                        comment_id,
                        media_id,
                        parent_id,
                        commenter_username,
                        comment_text,
                        payload_json,
                        source_event_id,
                        status,
                        updated_at
                    ) VALUES (
                        :comment_id,
                        :media_id,
                        :parent_id,
                        :commenter_username,
                        :comment_text,
                        CAST(:payload_json AS JSONB),
                        :source_event_id,
                        'todo',
                        NOW()
                    )
                    ON CONFLICT (comment_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "comment_id": comment_id,
                    "media_id": _clean_str(task.get("media_id")),
                    "parent_id": _clean_str(task.get("parent_id")),
                    "commenter_username": _clean_str(task.get("commenter_username")),
                    "comment_text": _clean_str(task.get("comment_text")),
                    "payload_json": _to_json(task.get("payload") or {}),
                    "source_event_id": source_event_id,
                },
            ).mappings().first()
            if row:
                inserted += 1
    return inserted


def claim_next_comment_task() -> dict[str, Any] | None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                WITH next_task AS (
                    SELECT id
                    FROM ig_comment_task
                    WHERE status = 'todo'
                    ORDER BY created_at ASC, id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ig_comment_task t
                SET
                    status = 'processing',
                    attempts = t.attempts + 1,
                    updated_at = NOW()
                FROM next_task
                WHERE t.id = next_task.id
                RETURNING
                    t.id,
                    t.comment_id,
                    t.media_id,
                    t.parent_id,
                    t.commenter_username,
                    t.comment_text,
                    t.payload_json,
                    t.source_event_id,
                    t.status,
                    t.attempts,
                    t.created_at
                """
            )
        ).mappings().first()
    return dict(row) if row else None


def mark_comment_task_done(
    task_id: int,
    *,
    reply_mode_snapshot: str,
    reply_text: str | None,
    reply_comment_id: str | None = None,
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ig_comment_task
                SET
                    status = 'done',
                    reply_mode_snapshot = :reply_mode_snapshot,
                    reply_text = :reply_text,
                    reply_comment_id = :reply_comment_id,
                    last_error = NULL,
                    processed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :task_id
                """
            ),
            {
                "task_id": task_id,
                "reply_mode_snapshot": _normalize_reply_mode(reply_mode_snapshot),
                "reply_text": reply_text,
                "reply_comment_id": reply_comment_id,
            },
        )


def mark_comment_task_error(task_id: int, error_message: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ig_comment_task
                SET
                    status = 'error',
                    last_error = :last_error,
                    processed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :task_id
                """
            ),
            {
                "task_id": task_id,
                "last_error": (error_message or "Unknown error")[:4000],
            },
        )


def get_comment_reply_mode() -> str:
    env_default = _normalize_reply_mode(os.getenv("IG_REPLY_MODE", "draft"))
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT value_text
                FROM bot_settings
                WHERE setting_key = 'comment_reply_mode'
                """
            )
        ).mappings().first()
    if not row or not row.get("value_text"):
        return env_default
    return _normalize_reply_mode(str(row["value_text"]))


def set_comment_reply_mode(mode: str) -> str:
    normalized = _normalize_reply_mode(mode)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO bot_settings (setting_key, value_text, updated_at)
                VALUES ('comment_reply_mode', :mode, NOW())
                ON CONFLICT (setting_key) DO UPDATE SET
                    value_text = EXCLUDED.value_text,
                    updated_at = NOW()
                """
            ),
            {"mode": normalized},
        )
    return normalized


def _normalize_reply_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode in {"off", "draft", "auto"}:
        return mode
    return "draft"


def _to_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None
