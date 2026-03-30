import json
import logging
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque
from urllib import error, request


logger = logging.getLogger(__name__)


class LLMUserFacingError(Exception):
    pass


@dataclass(slots=True)
class _UserMessage:
    role: str
    content: str


class LLMAdapter:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        system_prompt: str | None = None,
        memory_size: int = 8,
        rate_limit_max_requests: int = 5,
        rate_limit_window_seconds: int = 60,
        max_input_chars: int = 1500,
        max_output_chars: int = 1200,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.system_prompt = (
            system_prompt
            or "Ты полезный Telegram-бот. Отвечай кратко, по делу, безопасно. "
            "Не выдумывай факты; если не уверен, так и скажи."
        )
        self.memory_size = max(0, memory_size)
        self.rate_limit_max_requests = max(1, rate_limit_max_requests)
        self.rate_limit_window_seconds = max(1, rate_limit_window_seconds)
        self.max_input_chars = max(50, max_input_chars)
        self.max_output_chars = max(50, max_output_chars)
        self.timeout_seconds = max(5, timeout_seconds)

        self._lock = threading.Lock()
        self._history: dict[int, Deque[_UserMessage]] = defaultdict(
            lambda: deque(maxlen=self.memory_size or None)
        )
        self._requests: dict[int, Deque[float]] = defaultdict(deque)

    def reply(self, user_id: int, text: str) -> str:
        user_id = int(user_id)
        text = (text or "").strip()
        if not text:
            return "Пустое сообщение."

        input_truncated = False
        if len(text) > self.max_input_chars:
            text = text[: self.max_input_chars]
            input_truncated = True

        history_snapshot = self._reserve_request_and_get_history(user_id)
        if history_snapshot is None:
            return (
                "Слишком много запросов. Подождите немного и отправьте сообщение снова."
            )

        try:
            answer = self._call_model(history_snapshot, text)
        except LLMUserFacingError as exc:
            logger.warning("LLM user-facing error for user_id=%s: %s", user_id, exc)
            return str(exc)
        except Exception:
            logger.exception("LLM request failed for user_id=%s", user_id)
            return "Не удалось получить ответ от LLM. Попробуйте еще раз позже."

        if len(answer) > self.max_output_chars:
            answer = answer[: self.max_output_chars].rstrip() + "..."

        self._append_history(user_id, "user", text)
        self._append_history(user_id, "assistant", answer)

        if input_truncated:
            answer = "Ваше сообщение было сокращено по длине.\n\n" + answer
        return answer

    def _reserve_request_and_get_history(self, user_id: int) -> list[dict[str, str]] | None:
        now = time.time()
        with self._lock:
            timestamps = self._requests[user_id]
            while timestamps and now - timestamps[0] > self.rate_limit_window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self.rate_limit_max_requests:
                return None

            timestamps.append(now)
            history = list(self._history[user_id])

        return [{"role": msg.role, "content": msg.content} for msg in history]

    def _append_history(self, user_id: int, role: str, content: str) -> None:
        if self.memory_size == 0:
            return
        with self._lock:
            self._history[user_id].append(_UserMessage(role=role, content=content))

    def _call_model(self, history: list[dict[str, str]], user_text: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": 350,
        }

        url = f"{self.base_url}/chat/completions"
        req = request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            logger.error("LLM HTTP error %s: %s", exc.code, body)
            api_code = None
            try:
                payload = json.loads(body)
                api_code = payload.get("error", {}).get("code")
            except Exception:
                pass

            if exc.code == 401:
                raise LLMUserFacingError(
                    "Ошибка LLM API: неверный ключ или нет доступа к модели."
                ) from exc
            if exc.code == 429 and api_code == "insufficient_quota":
                raise LLMUserFacingError(
                    "Квота LLM закончилась или не подключен API billing."
                ) from exc
            if exc.code == 429:
                raise LLMUserFacingError(
                    "Сервис LLM временно ограничивает запросы. Попробуйте чуть позже."
                ) from exc
            if 500 <= exc.code < 600:
                raise LLMUserFacingError(
                    "Сервис LLM временно недоступен. Попробуйте позже."
                ) from exc
            raise
        except error.URLError as exc:
            logger.error("LLM network error: %s", exc)
            raise LLMUserFacingError(
                "Нет соединения с LLM API или запрос истек по времени."
            ) from exc
        except socket.timeout as exc:
            logger.error("LLM timeout: %s", exc)
            raise LLMUserFacingError(
                "LLM отвечает слишком долго. Попробуйте еще раз."
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected LLM response format: %r", data)
            raise RuntimeError("Unexpected LLM response format") from exc

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            content = "\n".join(part for part in parts if part)

        text = str(content).strip()
        return text or "Пустой ответ от модели."
