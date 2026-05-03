import logging
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

# Allow running as `python workers/comments_worker.py` from repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import (  # noqa: E402
    claim_next_comment_task,
    ensure_tables,
    get_comment_reply_mode,
    mark_comment_task_done,
    mark_comment_task_error,
)
from config import read_env_var, read_env_var_optional  # noqa: E402
from integrations.ai import build_llm_adapter_from_env  # noqa: E402
from integrations.dify import build_dify_chat_adapter_from_env, send_comment_to_dify  # noqa: E402
from integrations.instagram import InstagramGraphClient, reply_to_comment  # noqa: E402
from integrations.langfuse_support import (  # noqa: E402
    build_trace_context,
    get_langfuse_client,
    get_trace_url,
    propagate_langfuse_attributes,
    serialize_for_langfuse,
    update_observation,
)


logger = logging.getLogger(__name__)

load_dotenv()


class ReplyService(Protocol):
    def reply(self, user_id: int | str, text: str) -> str:
        """
        Возвращает текстовый ответ на входной запрос.

        Args:
            user_id (int | str): Идентификатор пользователя или комментария.
            text (str): Текст запроса для генерации ответа.

        Returns:
            str: Готовый текст ответа.
        """
        ...


class CommentsWorker:
    def __init__(self) -> None:
        """
        Инициализирует воркер обработки Instagram-комментариев.

        Args:
            None: Конструктор не принимает аргументов.

        Returns:
            None: Ничего не возвращает.
        """
        self.graph = InstagramGraphClient.from_env()
        self.reply_provider = _read_reply_provider()
        self.reply_service = self._build_reply_service()
        self.poll_seconds = _read_int_env("IG_WORKER_POLL_SECONDS", 3)
        self.idle_log_every = _read_int_env("IG_WORKER_IDLE_LOG_EVERY", 20)
        self._idle_ticks = 0
        self._log_reply_mode_configuration()
        self._log_reply_provider_configuration()

    def run_forever(self) -> None:
        """
        Запускает бесконечный цикл чтения и обработки задач из БД.

        Args:
            None: Метод не принимает аргументов.

        Returns:
            None: Ничего не возвращает.
        """
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
        """
        Обрабатывает одну задачу комментария до финального статуса.

        Args:
            task (dict[str, Any]): Словарь с данными задачи из очереди.

        Returns:
            None: Ничего не возвращает.
        """
        task_id = int(task["id"])
        comment_id = str(task["comment_id"])
        mode = get_comment_reply_mode()
        langfuse = get_langfuse_client()
        trace_seed = _build_task_trace_seed(task)
        observation_cm = (
            langfuse.start_as_current_observation(
                as_type="agent",
                name="instagram.comment.process",
                trace_context=build_trace_context(trace_seed),
                input=serialize_for_langfuse(task),
                metadata={
                    "provider": "instagram",
                    "comment_id": comment_id,
                    "reply_provider": self.reply_provider,
                },
            )
            if langfuse is not None
            else nullcontext(None)
        )

        logger.info(
            "Processing comment task id=%s comment_id=%s mode=%s attempts=%s",
            task_id,
            comment_id,
            mode,
            task.get("attempts"),
        )

        with observation_cm as observation:
            trace_url = (
                get_trace_url(getattr(observation, "trace_id", None))
                if observation is not None
                else None
            )
            if trace_url:
                logger.info(
                    "Langfuse comment trace: comment_id=%s url=%s",
                    comment_id,
                    trace_url,
                )

            with propagate_langfuse_attributes(
                user_id=_langfuse_user_id(task),
                session_id=f"instagram-comment:{comment_id}",
                metadata={
                    "service": "worker",
                    "provider": "instagram",
                    "reply_provider": self.reply_provider,
                    "reply_mode": mode,
                },
                trace_name="instagram-comment-worker",
            ):
                try:
                    if mode == "off":
                        mark_comment_task_done(
                            task_id,
                            reply_mode_snapshot=mode,
                            reply_text=None,
                            reply_comment_id=None,
                        )
                        update_observation(
                            observation,
                            level="DEBUG",
                            status_message="Reply mode is off",
                            output={"status": "skipped", "reason": "reply_mode_off"},
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
                        update_observation(
                            observation,
                            level="DEBUG",
                            status_message="Skipping self-authored comment",
                            output={"status": "skipped", "reason": "self_authored"},
                        )
                        return

                    if self.reply_provider != "dify":
                        send_comment_to_dify(
                            platform="instagram",
                            text=str(task.get("comment_text") or "").strip(),
                            author=str(task.get("commenter_username") or "").strip(),
                            comment_id=comment_id,
                        )

                    reply_text = self._build_reply(task)

                    if mode == "draft":
                        logger.info("Draft reply for comment_id=%s: %s", comment_id, reply_text)
                        mark_comment_task_done(
                            task_id,
                            reply_mode_snapshot=mode,
                            reply_text=reply_text,
                            reply_comment_id=None,
                        )
                        update_observation(
                            observation,
                            output={
                                "status": "draft",
                                "reply_text": reply_text,
                            },
                        )
                        return

                    sent = self._send_reply(comment_id, reply_text)
                    reply_comment_id = str(sent.get("id") or "") or None
                    mark_comment_task_done(
                        task_id,
                        reply_mode_snapshot=mode,
                        reply_text=reply_text,
                        reply_comment_id=reply_comment_id,
                    )
                    update_observation(
                        observation,
                        output={
                            "status": "sent",
                            "reply_text": reply_text,
                            "reply_comment_id": reply_comment_id,
                            "graph_response": serialize_for_langfuse(sent),
                        },
                    )
                    logger.info(
                        "Auto reply sent for comment_id=%s reply_comment_id=%s",
                        comment_id,
                        reply_comment_id,
                    )
                except Exception as exc:
                    logger.exception("Failed to process comment task id=%s", task_id)
                    mark_comment_task_error(task_id, str(exc))
                    update_observation(
                        observation,
                        level="ERROR",
                        status_message=str(exc)[:200],
                        output={"status": "error"},
                        metadata={"error_type": exc.__class__.__name__},
                    )

    def _build_reply(self, task: dict[str, Any]) -> str:
        """
        Генерирует текст ответа через выбранный reply provider.

        Args:
            task (dict[str, Any]): Словарь с данными комментария.

        Returns:
            str: Нормализованный текст ответа.
        """
        if self.reply_service is None:
            return "Спасибо за комментарий! Мы скоро ответим подробнее."

        prompt = self._build_reply_prompt(task)
        user_key = _safe_user_key(task.get("comment_id"))
        langfuse = get_langfuse_client()
        observation_cm = (
            langfuse.start_as_current_observation(
                as_type="chain",
                name="instagram.reply.build",
                input={
                    "comment_id": str(task.get("comment_id") or ""),
                    "provider": self.reply_provider,
                    "prompt": prompt,
                },
                metadata={
                    "provider": self.reply_provider,
                    "commenter_username": str(task.get("commenter_username") or ""),
                },
            )
            if langfuse is not None
            else nullcontext(None)
        )
        with observation_cm as observation:
            try:
                reply = self.reply_service.reply(user_key, prompt).strip()
            except Exception as exc:
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=str(exc)[:200],
                    metadata={"error_type": exc.__class__.__name__},
                )
                raise

            reply = " ".join(reply.split())
            if not reply:
                reply = "Спасибо за комментарий!"
            reply = reply[:1000]
            update_observation(
                observation,
                output={"reply_text": reply},
            )
            return reply

    def _build_reply_prompt(self, task: dict[str, Any]) -> str:
        """
        Собирает prompt для генерации ответа на комментарий.

        Args:
            task (dict[str, Any]): Словарь с данными комментария.

        Returns:
            str: Итоговый prompt для Dify или LLM.
        """
        comment_text = str(task.get("comment_text") or "").strip()
        username = str(task.get("commenter_username") or "").strip()

        return (
            "You are a brand assistant replying to Instagram comments.\n"
            "Write a short, polite, helpful reply in Russian.\n"
            "Do not invent facts and avoid toxic language.\n"
            "If the question is unclear, ask one clarifying question.\n\n"
            f"Username: {username or 'unknown'}\n"
            f"Comment: {comment_text or '[empty]'}"
        )

    def _build_reply_service(self) -> ReplyService | None:
        """
        Создаёт сервис ответов в зависимости от активного провайдера.

        Args:
            None: Метод не принимает аргументов.

        Returns:
            ReplyService | None: Настроенный адаптер ответов или `None`.
        """
        if self.reply_provider == "dify":
            return build_dify_chat_adapter_from_env(read_env_var, read_env_var_optional)
        return build_llm_adapter_from_env(read_env_var, read_env_var_optional)

    def _send_reply(self, comment_id: str, reply_text: str) -> dict[str, Any]:
        """
        Отправляет сгенерированный ответ в Instagram Graph API.

        Args:
            comment_id (str): Идентификатор исходного комментария.
            reply_text (str): Текст ответа.

        Returns:
            dict[str, Any]: Ответ Graph API после публикации reply-комментария.
        """
        langfuse = get_langfuse_client()
        observation_cm = (
            langfuse.start_as_current_observation(
                as_type="tool",
                name="instagram.reply.send",
                input={
                    "comment_id": comment_id,
                    "reply_text": reply_text,
                },
                metadata={"provider": "instagram"},
            )
            if langfuse is not None
            else nullcontext(None)
        )

        with observation_cm as observation:
            try:
                sent = reply_to_comment(self.graph, comment_id, reply_text)
            except Exception as exc:
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=str(exc)[:200],
                    metadata={"error_type": exc.__class__.__name__},
                )
                raise

            update_observation(
                observation,
                output=serialize_for_langfuse(sent),
            )
            return sent

    def _log_reply_mode_configuration(self) -> None:
        """
        Логирует эффективный режим ответа и расхождения с переменными окружения.

        Args:
            None: Метод не принимает аргументов.

        Returns:
            None: Ничего не возвращает.
        """
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

    def _log_reply_provider_configuration(self) -> None:
        """
        Логирует выбранного провайдера ответов и ошибки его конфигурации.

        Args:
            None: Метод не принимает аргументов.

        Returns:
            None: Ничего не возвращает.
        """
        logger.info("Comment reply provider: %s", self.reply_provider)
        if self.reply_service is None:
            logger.warning(
                "Reply provider %s is selected but not configured correctly",
                self.reply_provider,
            )


def _safe_user_key(raw_value: Any) -> int:
    """
    Преобразует произвольный идентификатор в стабильный числовой ключ.

    Args:
        raw_value (Any): Исходное значение идентификатора.

    Returns:
        int: Числовой ключ для адаптера ответов.
    """
    raw_text = str(raw_value or "").strip()
    if raw_text.isdigit():
        try:
            return int(raw_text)
        except ValueError:
            pass
    return abs(hash(raw_text or "ig-comment")) % (10**9)


def _read_int_env(name: str, default: int) -> int:
    """
    Читает целочисленную переменную окружения с защитой от некорректных значений.

    Args:
        name (str): Имя переменной окружения.
        default (int): Значение по умолчанию.

    Returns:
        int: Нормализованное целочисленное значение не меньше `1`.
    """
    raw_value = os.getenv(name, str(default))
    try:
        return max(1, int(raw_value or str(default)))
    except ValueError:
        return default


def _read_reply_provider() -> str:
    """
    Возвращает допустимого провайдера ответов для комментариев.

    Args:
        None: Функция не принимает аргументов.

    Returns:
        str: Значение `chat` или `dify`.
    """
    value = (os.getenv("IG_REPLY_PROVIDER") or "chat").strip().lower()
    if value in {"chat", "dify"}:
        return value
    logger.warning("Unknown IG_REPLY_PROVIDER=%r, using chat", value)
    return "chat"


def _build_task_trace_seed(task: dict[str, Any]) -> str:
    """
    Строит seed для трассировки задачи в Langfuse.

    Args:
        task (dict[str, Any]): Словарь с данными задачи.

    Returns:
        str: Seed в формате `instagram-event:*` или `instagram-comment:*`.
    """
    source_event_id = str(task.get("source_event_id") or "").strip()
    if source_event_id:
        return f"instagram-event:{source_event_id}"

    comment_id = str(task.get("comment_id") or "").strip()
    if comment_id:
        return f"instagram-comment:{comment_id}"

    return "instagram-comment:unknown"


def _langfuse_user_id(task: dict[str, Any]) -> str | None:
    """
    Вычисляет пользовательский идентификатор для тегирования trace.

    Args:
        task (dict[str, Any]): Словарь с данными задачи.

    Returns:
        str | None: Username комментатора или fallback на `comment_id`.
    """
    username = str(task.get("commenter_username") or "").strip()
    if username:
        return username
    comment_id = str(task.get("comment_id") or "").strip()
    return comment_id or None


def _is_self_authored_comment(task: dict[str, Any]) -> bool:
    """
    Проверяет, оставлен ли комментарий самим владельцем аккаунта.

    Args:
        task (dict[str, Any]): Словарь с данными задачи.

    Returns:
        bool: `True`, если комментарий authored тем же аккаунтом, что и media entry.
    """
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
    """
    Запускает воркер как самостоятельный процесс.

    Args:
        None: Функция не принимает аргументов.

    Returns:
        None: Ничего не возвращает.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    CommentsWorker().run_forever()


if __name__ == "__main__":
    main()
