import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Allow running as `python workers/comments_worker.py` from repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import (
    claim_next_comment_task,
    ensure_tables,
    get_comment_reply_mode,
    mark_comment_task_done,
    mark_comment_task_error,
)
from config import read_env_var, read_env_var_optional
from integrations.ai import build_llm_adapter_from_env
from integrations.instagram import InstagramGraphClient, reply_to_comment


logger = logging.getLogger(__name__)

load_dotenv()


class CommentsWorker:
    def __init__(self) -> None:
        self.graph = InstagramGraphClient.from_env()
        self.llm = build_llm_adapter_from_env(read_env_var, read_env_var_optional)
        self.poll_seconds = _read_int_env("IG_WORKER_POLL_SECONDS", 3)
        self.idle_log_every = _read_int_env("IG_WORKER_IDLE_LOG_EVERY", 20)
        self._idle_ticks = 0
        self._log_reply_mode_configuration()

    def run_forever(self) -> None:
        ensure_tables()
        logger.info("Comments worker started (poll=%ss)", self.poll_seconds)
        while True:
            task = claim_next_comment_task()
            if not task:
                self._idle_ticks += 1
                if self._idle_ticks >= self.idle_log_every:
                    self._idle_ticks = 0
                    logger.info("Comments worker idle (no tasks)")
                time.sleep(self.poll_seconds)
                continue

            self._idle_ticks = 0
            self._process_task(task)

    def _process_task(self, task: dict[str, Any]) -> None:
        task_id = int(task["id"])
        comment_id = str(task["comment_id"])
        mode = get_comment_reply_mode()
        logger.info(
            "Processing comment task id=%s comment_id=%s mode=%s attempts=%s",
            task_id,
            comment_id,
            mode,
            task.get("attempts"),
        )

        try:
            if mode == "off":
                mark_comment_task_done(
                    task_id,
                    reply_mode_snapshot=mode,
                    reply_text=None,
                    reply_comment_id=None,
                )
                return

            if _is_self_authored_comment(task):
                logger.info(
                    "Skipping self-authored comment id=%s username=%s",
                    comment_id,
                    task.get("commenter_username"),
                )
                mark_comment_task_done(
                    task_id,
                    reply_mode_snapshot=mode,
                    reply_text=None,
                    reply_comment_id=None,
                )
                return

            reply_text = self._build_reply(task)

            if mode == "draft":
                logger.info("Draft reply for comment_id=%s: %s", comment_id, reply_text)
                mark_comment_task_done(
                    task_id,
                    reply_mode_snapshot=mode,
                    reply_text=reply_text,
                    reply_comment_id=None,
                )
                return

            sent = reply_to_comment(self.graph, comment_id, reply_text)
            reply_comment_id = str(sent.get("id") or "") or None
            mark_comment_task_done(
                task_id,
                reply_mode_snapshot=mode,
                reply_text=reply_text,
                reply_comment_id=reply_comment_id,
            )
            logger.info(
                "Auto reply sent for comment_id=%s reply_comment_id=%s",
                comment_id,
                reply_comment_id,
            )
        except Exception as exc:
            logger.exception("Failed to process comment task id=%s", task_id)
            mark_comment_task_error(task_id, str(exc))

    def _build_reply(self, task: dict[str, Any]) -> str:
        comment_text = str(task.get("comment_text") or "").strip()
        username = str(task.get("commenter_username") or "").strip()

        if self.llm is None:
            return "Спасибо за комментарий! Мы скоро ответим подробнее."

        prompt = (
            "Ты помощник бренда и отвечаешь на комментарии в Instagram.\n"
            "Напиши короткий, вежливый, полезный ответ на русском языке.\n"
            "Без выдуманных фактов и токсичности.\n"
            "Если вопрос неясен, задай один уточняющий вопрос.\n\n"
            f"Имя пользователя: {username or 'неизвестно'}\n"
            f"Комментарий: {comment_text or '[пусто]'}"
        )

        user_key = _safe_user_key(task.get("comment_id"))
        reply = self.llm.reply(user_key, prompt).strip()
        reply = " ".join(reply.split())
        if not reply:
            reply = "Спасибо за комментарий!"
        return reply[:1000]

    def _log_reply_mode_configuration(self) -> None:
        env_mode = (os.getenv("IG_REPLY_MODE") or "").strip().lower() or "<unset>"
        effective_mode = get_comment_reply_mode()
        if env_mode != effective_mode:
            logger.warning(
                "Comment reply mode differs: env=%s effective=%s (DB setting wins)",
                env_mode,
                effective_mode,
            )
        else:
            logger.info("Comment reply mode: %s", effective_mode)


def _safe_user_key(raw_value: Any) -> int:
    raw_text = str(raw_value or "").strip()
    if raw_text.isdigit():
        try:
            return int(raw_text)
        except ValueError:
            pass
    return abs(hash(raw_text or "ig-comment")) % (10**9)


def _read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return max(1, int(raw_value or str(default)))
    except ValueError:
        return default


def _is_self_authored_comment(task: dict[str, Any]) -> bool:
    payload = task.get("payload_json")
    if not isinstance(payload, dict):
        return False

    value = payload.get("value")
    if not isinstance(value, dict):
        return False

    from_data = value.get("from")
    if not isinstance(from_data, dict):
        return False

    author_id = str(from_data.get("id") or "").strip()
    entry_id = str(payload.get("entry_id") or "").strip()
    return bool(author_id and entry_id and author_id == entry_id)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    CommentsWorker().run_forever()


if __name__ == "__main__":
    main()
