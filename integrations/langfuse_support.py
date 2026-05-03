import hashlib
import inspect
import logging
import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

try:
    from langfuse import Langfuse
except ImportError:  # pragma: no cover - runtime fallback
    Langfuse = None

try:
    from langfuse import propagate_attributes as sdk_propagate_attributes
except ImportError:  # pragma: no cover - SDK v2 fallback
    sdk_propagate_attributes = None


logger = logging.getLogger(__name__)

_CURRENT_TRACE: ContextVar[Any | None] = ContextVar("langfuse_trace", default=None)
_CURRENT_OBSERVATION: ContextVar[Any | None] = ContextVar(
    "langfuse_observation",
    default=None,
)


class _V2ObservationWrapper:
    def __init__(
        self,
        *,
        raw: Any,
        trace: Any,
        client: "_LangfuseClientAdapter",
        is_root: bool,
        kind: str,
    ) -> None:
        self._raw = raw
        self._trace = trace
        self._client = client
        self._is_root = is_root
        self._kind = kind

    @property
    def id(self) -> str | None:
        return getattr(self._raw, "id", None)

    @property
    def trace_id(self) -> str | None:
        return getattr(self._raw, "trace_id", None) or getattr(self._trace, "id", None)

    def update(self, **kwargs: Any) -> None:
        observation_kwargs = _normalize_v2_observation_kwargs(self._kind, kwargs)
        if observation_kwargs:
            self._raw.update(**observation_kwargs)

        if self._is_root:
            trace_kwargs = _normalize_v2_trace_kwargs(kwargs)
            if trace_kwargs:
                self._trace.update(**trace_kwargs)

    def end(self, **kwargs: Any) -> None:
        observation_kwargs = _normalize_v2_observation_kwargs(self._kind, kwargs)
        if hasattr(self._raw, "end"):
            self._raw.end(**observation_kwargs)
        elif observation_kwargs:
            self._raw.update(**observation_kwargs)

        if self._is_root:
            trace_kwargs = _normalize_v2_trace_kwargs(kwargs)
            if trace_kwargs:
                self._trace.update(**trace_kwargs)

    def get_trace_url(self) -> str | None:
        if hasattr(self._raw, "get_trace_url"):
            try:
                return self._raw.get_trace_url()
            except Exception:
                pass
        return self._client.build_trace_url(self.trace_id)


class _V2ObservationContext:
    def __init__(
        self,
        client: "_LangfuseClientAdapter",
        *,
        as_type: str,
        name: str,
        trace_context: dict[str, str] | None = None,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        status_message: str | None = None,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        usage_details: dict[str, int] | None = None,
        completion_start_time: datetime | None = None,
    ) -> None:
        self._client = client
        self._as_type = as_type
        self._name = name
        self._trace_context = trace_context
        self._input = input
        self._output = output
        self._metadata = metadata
        self._level = level
        self._status_message = status_message
        self._model = model
        self._model_parameters = model_parameters
        self._usage_details = usage_details
        self._completion_start_time = completion_start_time
        self._trace_token: Any = None
        self._observation_token: Any = None
        self._observation: _V2ObservationWrapper | None = None

    def __enter__(self) -> _V2ObservationWrapper:
        parent_observation = _CURRENT_OBSERVATION.get()
        current_trace = _CURRENT_TRACE.get()
        is_root = parent_observation is None

        if current_trace is None:
            current_trace = self._client.create_trace(
                name=self._name,
                trace_context=self._trace_context,
                input=self._input,
                metadata=self._metadata,
            )

        creator = parent_observation._raw if parent_observation is not None else current_trace
        raw_observation = self._client.create_v2_observation(
            creator=creator,
            as_type=self._as_type,
            name=self._name,
            input=self._input,
            output=self._output,
            metadata=self._metadata,
            level=self._level,
            status_message=self._status_message,
            model=self._model,
            model_parameters=self._model_parameters,
            usage_details=self._usage_details,
            completion_start_time=self._completion_start_time,
        )

        observation = _V2ObservationWrapper(
            raw=raw_observation,
            trace=current_trace,
            client=self._client,
            is_root=is_root,
            kind=self._as_type,
        )
        self._trace_token = _CURRENT_TRACE.set(current_trace)
        self._observation_token = _CURRENT_OBSERVATION.set(observation)
        self._observation = observation
        return observation

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            if self._observation is not None:
                self._observation.end()
        except Exception:
            logger.debug("Failed to end Langfuse v2 observation", exc_info=True)
        finally:
            if self._observation_token is not None:
                _CURRENT_OBSERVATION.reset(self._observation_token)
            if self._trace_token is not None:
                _CURRENT_TRACE.reset(self._trace_token)
        return False


class _LangfuseClientAdapter:
    def __init__(
        self,
        raw_client: Any,
        *,
        public_key: str,
        secret_key: str,
        base_url: str,
    ) -> None:
        self._raw = raw_client
        self._public_key = public_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._supports_observation_context = hasattr(raw_client, "start_as_current_observation")
        self._project_id_cache: str | None = None
        self._project_id_loaded = False

    def create_trace_id(self, seed: str) -> str:
        if hasattr(self._raw, "create_trace_id"):
            return self._raw.create_trace_id(seed=seed)
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    def start_as_current_observation(self, **kwargs: Any):
        if self._supports_observation_context:
            return self._raw.start_as_current_observation(**kwargs)
        return _V2ObservationContext(self, **kwargs)

    def create_trace(
        self,
        *,
        name: str,
        trace_context: dict[str, str] | None,
        input: Any,
        metadata: dict[str, Any] | None,
    ) -> Any:
        trace_id = None
        if isinstance(trace_context, dict):
            trace_id = str(trace_context.get("trace_id") or "").strip() or None

        kwargs = {
            "id": trace_id,
            "name": name,
            "input": input,
            "metadata": metadata,
        }
        return self._raw.trace(**_drop_none(kwargs))

    def create_v2_observation(
        self,
        *,
        creator: Any,
        as_type: str,
        name: str,
        input: Any,
        output: Any,
        metadata: dict[str, Any] | None,
        level: str | None,
        status_message: str | None,
        model: str | None,
        model_parameters: dict[str, Any] | None,
        usage_details: dict[str, int] | None,
        completion_start_time: datetime | None,
    ) -> Any:
        observation_type = _map_observation_type(as_type)
        common_kwargs = _drop_none(
            {
                "name": name,
                "input": input,
                "output": output,
                "metadata": metadata,
                "level": _normalize_level(level),
                "status_message": status_message,
            }
        )

        if observation_type == "generation":
            return creator.generation(
                **_drop_none(
                    {
                        **common_kwargs,
                        "model": model,
                        "model_parameters": model_parameters,
                        "usage_details": usage_details,
                        "completion_start_time": completion_start_time,
                    }
                )
            )
        if observation_type == "event":
            return creator.event(**common_kwargs)
        return creator.span(**common_kwargs)

    def build_trace_url(self, trace_id: str | None) -> str | None:
        if not trace_id:
            return None

        if self._supports_observation_context:
            try:
                return self._raw.get_trace_url(trace_id=trace_id)
            except Exception:
                logger.debug(
                    "Failed to build Langfuse v4 trace URL for trace_id=%r",
                    trace_id,
                    exc_info=True,
                )

        current_observation = _CURRENT_OBSERVATION.get()
        if current_observation is not None and current_observation.trace_id == trace_id:
            try:
                return current_observation.get_trace_url()
            except Exception:
                logger.debug(
                    "Failed to build Langfuse v2 trace URL from current observation",
                    exc_info=True,
                )

        if not self._supports_observation_context:
            return f"{self._base_url}/trace/{trace_id}"

        project_id = self._get_project_id()
        if not project_id:
            return None
        return f"{self._base_url}/project/{project_id}/traces/{trace_id}"

    @contextmanager
    def propagate_attributes(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, str] | None = None,
        tags: list[str] | None = None,
        trace_name: str | None = None,
    ):
        current_trace = _CURRENT_TRACE.get()
        if current_trace is None:
            yield
            return

        update_kwargs = _drop_none(
            {
                "user_id": user_id,
                "session_id": session_id,
                "metadata": metadata,
                "tags": tags,
                "name": trace_name,
            }
        )
        if update_kwargs:
            try:
                current_trace.update(**update_kwargs)
            except Exception:
                logger.debug("Failed to propagate Langfuse v2 trace attributes", exc_info=True)

        yield

    def _get_project_id(self) -> str | None:
        if self._project_id_loaded:
            return self._project_id_cache

        self._project_id_loaded = True
        try:
            response = requests.get(
                f"{self._base_url}/api/public/projects",
                auth=(self._public_key, self._secret_key),
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data")
            if isinstance(data, list) and data:
                project = data[0]
                if isinstance(project, dict):
                    project_id = str(project.get("id") or "").strip()
                    if project_id:
                        self._project_id_cache = project_id
                        return project_id
        except Exception:
            logger.debug("Failed to resolve Langfuse project id", exc_info=True)
        return self._project_id_cache


@lru_cache(maxsize=1)
def get_langfuse_client() -> Any | None:
    """
    Создаёт и кэширует клиент Langfuse на основе переменных окружения.

    Args:
        None: Функция не принимает аргументов.

    Returns:
        Any | None: Адаптированный клиент Langfuse или `None`, если интеграция не настроена.
    """
    if Langfuse is None:
        logger.warning("Langfuse SDK is not installed")
        return None

    public_key = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    base_url = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").strip()

    if not public_key or not secret_key or not base_url:
        return None

    try:
        init_signature = inspect.signature(Langfuse.__init__)
        client_kwargs = {
            "public_key": public_key,
            "secret_key": secret_key,
            "debug": _is_truthy("LANGFUSE_DEBUG"),
        }
        if "base_url" in init_signature.parameters:
            client_kwargs["base_url"] = base_url
        else:
            client_kwargs["host"] = base_url
        raw_client = Langfuse(**client_kwargs)
        return _LangfuseClientAdapter(
            raw_client,
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
        )
    except Exception:
        logger.exception("Failed to initialize Langfuse client")
        return None


def build_trace_context(seed: str | None) -> dict[str, str] | None:
    """
    Строит trace context для повторяемой трассировки по стабильному seed.

    Args:
        seed (str | None): Стабильная строка для генерации `trace_id`.

    Returns:
        dict[str, str] | None: Контекст трассировки для Langfuse или `None`, если клиент недоступен.
    """
    client = get_langfuse_client()
    if client is None or not seed:
        return None

    try:
        trace_id = client.create_trace_id(seed=seed)
        if getattr(client, "_supports_observation_context", False):
            return {
                "trace_id": trace_id,
                "parent_span_id": "0123456789abcdef",
            }
        return {"trace_id": trace_id}
    except Exception:
        logger.exception("Failed to create Langfuse trace context for seed=%r", seed)
        return None


def get_trace_url(trace_id: str | None) -> str | None:
    """
    Возвращает URL для открытия трейса в интерфейсе Langfuse.

    Args:
        trace_id (str | None): Идентификатор трейса.

    Returns:
        str | None: Ссылка на trace или `None`, если её нельзя построить.
    """
    client = get_langfuse_client()
    if client is None or not trace_id:
        return None

    try:
        return client.build_trace_url(trace_id)
    except Exception:
        logger.debug("Langfuse trace URL is unavailable for trace_id=%r", trace_id, exc_info=True)
        return None


def propagate_langfuse_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    trace_name: str | None = None,
):
    """
    Добавляет пользовательские атрибуты к текущей трассировке Langfuse.

    Args:
        user_id (str | None, optional): Идентификатор пользователя для trace.
        session_id (str | None, optional): Идентификатор сессии или треда.
        metadata (dict[str, Any] | None, optional): Дополнительные метаданные trace.
        tags (list[str] | None, optional): Набор тегов для фильтрации trace.
        trace_name (str | None, optional): Человекочитаемое имя trace.

    Returns:
        Any: Контекстный менеджер, безопасный даже при выключенной интеграции.
    """
    client = get_langfuse_client()
    if client is None:
        return nullcontext()

    compact_metadata = _compact_metadata(metadata or {})
    if getattr(client, "_supports_observation_context", False) and sdk_propagate_attributes is not None:
        return sdk_propagate_attributes(
            user_id=_truncate_text(user_id, 200),
            session_id=_truncate_text(session_id, 200),
            metadata=compact_metadata or None,
            tags=tags,
            trace_name=trace_name,
        )

    return client.propagate_attributes(
        user_id=_truncate_text(user_id, 200),
        session_id=_truncate_text(session_id, 200),
        metadata=compact_metadata or None,
        tags=tags,
        trace_name=trace_name,
    )


def update_observation(observation: Any, **kwargs: Any) -> None:
    """
    Безопасно обновляет текущую observation/span в Langfuse.

    Args:
        observation (Any): Observation-объект Langfuse.
        **kwargs (Any): Поля для обновления observation.

    Returns:
        None: Ничего не возвращает.
    """
    if observation is None:
        return

    try:
        observation.update(**kwargs)
    except Exception:
        logger.debug("Failed to update Langfuse observation", exc_info=True)


def serialize_for_langfuse(value: Any) -> Any:
    """
    Преобразует произвольное Python-значение в сериализуемый вид для Langfuse.

    Args:
        value (Any): Исходное значение.

    Returns:
        Any: Значение, пригодное для отправки в Langfuse metadata или input/output.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): serialize_for_langfuse(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [serialize_for_langfuse(item) for item in value]
    return str(value)


def _normalize_v2_observation_kwargs(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    observation_type = _map_observation_type(kind)
    allowed = {
        "name",
        "input",
        "output",
        "metadata",
        "level",
        "status_message",
    }
    if observation_type == "generation":
        allowed.update({"model", "model_parameters", "usage_details", "completion_start_time"})

    normalized: dict[str, Any] = {}
    for key in allowed:
        if key not in kwargs:
            continue
        value = kwargs[key]
        if key == "level":
            value = _normalize_level(value)
        if value is None:
            continue
        normalized[key] = value
    return normalized


def _normalize_v2_trace_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in ("name", "input", "output", "metadata", "tags"):
        value = kwargs.get(key)
        if value is not None:
            normalized[key] = value
    return normalized


def _map_observation_type(as_type: str) -> str:
    if as_type == "generation":
        return "generation"
    if as_type == "event":
        return "event"
    return "span"


def _normalize_level(value: Any) -> str | None:
    if value is None:
        return None
    level = str(value).strip().upper()
    if level in {"DEBUG", "DEFAULT", "WARNING", "ERROR"}:
        return level
    if level == "INFO":
        return "DEFAULT"
    if level == "WARN":
        return "WARNING"
    return None


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _compact_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    compact: dict[str, str] = {}
    for key, value in metadata.items():
        key_text = "".join(ch for ch in str(key) if ch.isalnum())
        if not key_text:
            continue

        value_text = _truncate_text(_metadata_value_to_text(value), 200)
        if value_text is None:
            continue
        compact[key_text] = value_text
    return compact


def _metadata_value_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _truncate_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _is_truthy(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}
