import logging
import os
from contextlib import nullcontext
from typing import Any, Callable

import requests

from integrations.ai.adapter import LLMUserFacingError
from integrations.langfuse_support import (
    get_langfuse_client,
    serialize_for_langfuse,
    update_observation,
)


logger = logging.getLogger(__name__)


EnvReader = Callable[[str], str]
EnvOptionalReader = Callable[[str, str | None], str | None]


class DifyChatAdapter:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.dify.ai/v1",
        response_mode: str = "blocking",
        max_input_chars: int = 1500,
        max_output_chars: int = 1200,
        timeout_seconds: int = 30,
    ) -> None:
        """
        Инициализирует адаптер для Dify Chat API.

        Args:
            api_key (str): API-ключ опубликованного Dify-приложения.
            base_url (str, optional): Базовый URL Dify API.
            response_mode (str, optional): Режим ответа Dify, например `blocking`.
            max_input_chars (int, optional): Максимальная длина входного текста.
            max_output_chars (int, optional): Максимальная длина итогового ответа.
            timeout_seconds (int, optional): Таймаут HTTP-запроса к Dify.

        Returns:
            None: Ничего не возвращает.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.response_mode = response_mode or "blocking"
        self.max_input_chars = max(50, max_input_chars)
        self.max_output_chars = max(50, max_output_chars)
        self.timeout_seconds = max(5, timeout_seconds)

    def reply(self, user_id: int | str, text: str) -> str:
        """
        Отправляет пользовательский текст в Dify и возвращает сгенерированный ответ.

        Args:
            user_id (int | str): Идентификатор пользователя в контексте Dify.
            text (str): Текст запроса.

        Returns:
            str: Ответ Dify или понятное пользователю сообщение об ошибке.

        Raises:
            LLMUserFacingError: Если Dify вернул ошибку, которую нужно показать пользователю.
            RuntimeError: Если Dify вернул неожиданный JSON-формат.
        """
        text = (text or "").strip()
        if not text:
            return "Пустое сообщение."

        if len(text) > self.max_input_chars:
            text = text[: self.max_input_chars]

        payload = {
            "inputs": {},
            "query": text,
            "response_mode": self.response_mode,
            "user": str(user_id),
        }

        langfuse = get_langfuse_client()
        observation_cm = (
            langfuse.start_as_current_observation(
                as_type="generation",
                name="dify.chat",
                input=payload,
                model="dify-chat",
                model_parameters={
                    "response_mode": self.response_mode,
                },
                metadata={
                    "provider": "dify",
                    "base_url": self.base_url,
                },
            )
            if langfuse is not None
            else nullcontext(None)
        )

        with observation_cm as observation:
            try:
                response = requests.post(
                    f"{self.base_url}/chat-messages",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                body = exc.response.text if exc.response is not None else ""
                logger.error("Dify chat HTTP error %s: %s", status_code, body)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message=f"HTTP {status_code}",
                    output={"error_body": body[:4000]},
                )

                if status_code == 401:
                    raise LLMUserFacingError(
                        "Ошибка Dify API: неверный ключ или нет доступа к приложению."
                    ) from exc
                if status_code == 404:
                    raise LLMUserFacingError(
                        "Dify API не найден. Проверьте DIFY_API_BASE_URL и тип приложения."
                    ) from exc
                if status_code == 429:
                    raise LLMUserFacingError(
                        "Dify временно ограничивает запросы. Попробуйте чуть позже."
                    ) from exc
                if status_code is not None and 500 <= status_code < 600:
                    raise LLMUserFacingError(
                        "Dify временно недоступен. Попробуйте позже."
                    ) from exc
                raise
            except requests.exceptions.Timeout as exc:
                logger.error("Dify chat timeout: %s", exc)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message="Timeout",
                )
                raise LLMUserFacingError(
                    "Dify отвечает слишком долго. Попробуйте еще раз."
                ) from exc
            except requests.exceptions.RequestException as exc:
                logger.error("Dify chat network error: %s", exc)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message="Network error",
                )
                raise LLMUserFacingError(
                    "Нет соединения с Dify API или запрос истек по времени."
                ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                logger.error("Invalid Dify chat JSON response: %r", response.text)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message="Invalid JSON response",
                    output={"raw_response": response.text[:4000]},
                )
                raise RuntimeError("Invalid Dify response format") from exc

            logger.debug("Dify chat response: %s", data)
            answer = _extract_dify_answer(data)
            if answer is None:
                logger.error("Unexpected Dify chat response format: %r", data)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message="Missing answer field",
                    output=serialize_for_langfuse(data),
                )
                raise RuntimeError("Unexpected Dify response format")

            if not answer:
                error_message = _build_empty_answer_error_message(data)
                logger.error("%s: %r", error_message, data)
                update_observation(
                    observation,
                    level="ERROR",
                    status_message="Empty Dify answer",
                    output=serialize_for_langfuse(data),
                    metadata={
                        "provider": "dify",
                        "conversation_id": str(data.get("conversation_id") or ""),
                        "message_id": str(data.get("message_id") or ""),
                        "mode": str(data.get("mode") or ""),
                    },
                )
                raise RuntimeError(error_message)

            if len(answer) > self.max_output_chars:
                answer = answer[: self.max_output_chars].rstrip() + "..."

            update_observation(
                observation,
                output=answer,
                metadata={
                    "provider": "dify",
                    "conversation_id": str(data.get("conversation_id") or ""),
                    "message_id": str(data.get("message_id") or ""),
                },
            )
            return answer


def build_dify_chat_adapter_from_env(
    read_env_var: EnvReader,
    read_env_var_optional: EnvOptionalReader,
) -> DifyChatAdapter | None:
    """
    Создаёт Dify chat adapter на основе переменных окружения.

    Args:
        read_env_var (EnvReader): Функция чтения обязательной переменной окружения.
        read_env_var_optional (EnvOptionalReader): Функция чтения необязательной переменной окружения.

    Returns:
        DifyChatAdapter | None: Готовый адаптер или `None`, если ключ Dify не настроен.
    """
    try:
        api_key = read_env_var("DIFY_API_KEY")
    except (KeyError, ValueError, FileNotFoundError) as exc:
        logger.warning("DIFY_API_KEY is not configured: %s", exc)
        return None

    base_url = (
        read_env_var_optional("DIFY_API_BASE_URL", "https://api.dify.ai/v1")
        or "https://api.dify.ai/v1"
    )
    response_mode = read_env_var_optional("DIFY_RESPONSE_MODE", "blocking") or "blocking"
    timeout_seconds_raw = read_env_var_optional("DIFY_TIMEOUT_SECONDS", "30") or "30"
    try:
        timeout_seconds = max(5, int(timeout_seconds_raw))
    except ValueError:
        timeout_seconds = 30

    return DifyChatAdapter(
        api_key=api_key,
        base_url=base_url,
        response_mode=response_mode,
        timeout_seconds=timeout_seconds,
    )


def send_comment_to_dify(
    platform: str,
    text: str,
    author: str,
    comment_id: str,
) -> bool:
    """
    Отправляет комментарий в Dify webhook как побочный интеграционный вызов.

    Args:
        platform (str): Имя платформы-источника комментария.
        text (str): Текст комментария.
        author (str): Имя автора комментария.
        comment_id (str): Идентификатор комментария.

    Returns:
        bool: `True`, если webhook вызван успешно, иначе `False`.
    """
    webhook_url = (os.getenv("DIFY_WEBHOOK_URL") or "").strip()

    if not webhook_url:
        logger.error("DIFY_WEBHOOK_URL not configured")
        return False

    payload = {
        "platform": platform,
        "text": text,
        "author": author,
        "comment_id": comment_id,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        logger.info(
            "Comment sent to Dify webhook: %s comment_id=%s status=%s",
            webhook_url,
            comment_id,
            response.status_code,
        )
        logger.debug("Dify webhook response: %s", response.text)
        return True
    except requests.exceptions.RequestException as exc:
        logger.error(
            "Failed to send comment to Dify webhook: %s url=%s payload=%s",
            exc,
            webhook_url,
            payload,
        )
        return False


def _extract_dify_answer(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None

    answer = data.get("answer")
    if answer is None:
        return None

    return str(answer).strip()


def _build_empty_answer_error_message(data: dict[str, Any]) -> str:
    mode = str(data.get("mode") or "").strip() or "<unknown>"
    event = str(data.get("event") or "").strip() or "<unknown>"
    conversation_id = str(data.get("conversation_id") or "").strip() or "<unknown>"

    return (
        "Dify returned a successful chat response with an empty answer "
        f"(event={event}, mode={mode}, conversation_id={conversation_id}). "
        "Check the Dify app configuration: for Chatflow apps every executed branch "
        "should emit content through an Answer node; workflow apps should expose "
        "outputs via the workflow API instead of /chat-messages."
    )
