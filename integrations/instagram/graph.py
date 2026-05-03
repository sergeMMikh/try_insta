import os
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()


class GraphAPIError(RuntimeError):
    def __init__(self, error: dict[str, Any]):
        """
        Оборачивает ошибку Graph API в исключение Python.

        Args:
            error (dict[str, Any]): Поле `error` из ответа Graph API.

        Returns:
            None: Ничего не возвращает.
        """
        self.error = error
        super().__init__(str(error.get("message") or error))

    @property
    def code(self) -> int | None:
        """
        Возвращает числовой код ошибки Graph API, если он присутствует.

        Args:
            None: Свойство не принимает аргументов.

        Returns:
            int | None: Код ошибки или `None`, если он отсутствует либо некорректен.
        """
        code = self.error.get("code")
        try:
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None


class InstagramGraphClient:
    def __init__(
        self,
        token: str,
        graph_version: str = "v25.0",
        timeout_seconds: int = 30,
    ) -> None:
        """
        Инициализирует клиент для работы с Instagram Graph API.

        Args:
            token (str): Access token Meta Graph API.
            graph_version (str, optional): Версия Graph API в URL.
            timeout_seconds (int, optional): Таймаут HTTP-запросов в секундах.

        Returns:
            None: Ничего не возвращает.

        Raises:
            ValueError: Если токен не передан.
        """
        if not token:
            raise ValueError("META_TOKEN is required")
        self.token = token
        self.graph_version = graph_version.strip() or "v25.0"
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.base_url = f"https://graph.facebook.com/{self.graph_version}"

    @classmethod
    def from_env(cls) -> "InstagramGraphClient":
        """
        Создаёт клиент Graph API на основе переменных окружения.

        Args:
            None: Метод не принимает дополнительных аргументов.

        Returns:
            InstagramGraphClient: Инициализированный клиент Graph API.

        Raises:
            RuntimeError: Если `META_TOKEN` отсутствует в окружении.
        """
        token = os.getenv("META_TOKEN", "").strip()
        graph_version = (os.getenv("META_GRAPH_VERSION") or "v25.0").strip()
        if not token:
            raise RuntimeError("META_TOKEN is not set")
        return cls(token=token, graph_version=graph_version)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Выполняет GET-запрос к Graph API.

        Args:
            path (str): Относительный путь API.
            params (dict[str, Any] | None, optional): Query-параметры запроса.

        Returns:
            dict[str, Any]: JSON-ответ Graph API.
        """
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Выполняет POST-запрос к Graph API.

        Args:
            path (str): Относительный путь API.
            data (dict[str, Any] | None, optional): Form-data или тело POST-запроса.
            params (dict[str, Any] | None, optional): Query-параметры запроса.

        Returns:
            dict[str, Any]: JSON-ответ Graph API.
        """
        return self._request("POST", path, params=params, data=data)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Выполняет низкоуровневый HTTP-запрос к Graph API и валидирует ответ.

        Args:
            method (str): HTTP-метод запроса.
            path (str): Относительный путь API.
            params (dict[str, Any] | None, optional): Query-параметры.
            data (dict[str, Any] | None, optional): Тело запроса.

        Returns:
            dict[str, Any]: Успешный JSON-ответ Graph API.

        Raises:
            RuntimeError: Если сетевой запрос не удался или сервер вернул не-JSON ответ.
            GraphAPIError: Если Graph API вернул ошибку.
        """
        path = path.lstrip("/")
        query = dict(params or {})
        query["access_token"] = self.token
        url = f"{self.base_url}/{path}"
        try:
            response = requests.request(
                method=method,
                url=url,
                params=query,
                data=data,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Graph API request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Graph API returned non-JSON response (HTTP {response.status_code})"
            ) from exc

        if response.status_code >= 400 or "error" in payload:
            raise GraphAPIError(payload.get("error", payload))
        return payload
