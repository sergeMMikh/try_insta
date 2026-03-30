import logging
from dataclasses import dataclass
from typing import Callable

from integrations.ai import LLMConfig
from integrations.telegram_auth import TelegramAuthConfig


logger = logging.getLogger(__name__)


EnvReader = Callable[[str], str]
EnvOptionalReader = Callable[[str, str | None], str | None]


@dataclass(slots=True)
class AppSettings:
    telegram_auth: TelegramAuthConfig
    llm: LLMConfig


def _read_int_optional(
    read_env_var_optional: EnvOptionalReader,
    name: str,
    default: int,
) -> int:
    raw_value = read_env_var_optional(name, str(default))
    try:
        return int(raw_value or str(default))
    except ValueError:
        logger.warning("%s is invalid, using default=%s", name, default)
        return default


def load_app_settings(
    read_env_var: EnvReader,
    read_env_var_optional: EnvOptionalReader,
) -> AppSettings:
    bot_token = read_env_var("BOT_TOKEN")

    try:
        admin_id = int(read_env_var("ADMIN_ID"))
    except (KeyError, ValueError, FileNotFoundError) as exc:
        admin_id = None
        logger.warning("ADMIN_ID is not configured correctly: %s", exc)

    try:
        llm_api_key = read_env_var("OPENAI_API_KEY")
    except (KeyError, ValueError, FileNotFoundError) as exc:
        llm_api_key = None
        logger.warning("OPENAI_API_KEY is not configured: %s", exc)

    telegram_auth = TelegramAuthConfig(bot_token=bot_token, admin_id=admin_id)
    llm = LLMConfig(
        api_key=llm_api_key,
        model=read_env_var_optional("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        base_url=(
            read_env_var_optional("OPENAI_BASE_URL", "https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        ),
        system_prompt=read_env_var_optional("LLM_SYSTEM_PROMPT"),
        memory_size=_read_int_optional(read_env_var_optional, "LLM_MEMORY_SIZE", 8),
        rate_limit_max_requests=_read_int_optional(
            read_env_var_optional,
            "LLM_RATE_LIMIT_MAX_REQUESTS",
            5,
        ),
        rate_limit_window_seconds=_read_int_optional(
            read_env_var_optional,
            "LLM_RATE_LIMIT_WINDOW_SECONDS",
            60,
        ),
        max_input_chars=_read_int_optional(
            read_env_var_optional,
            "LLM_MAX_INPUT_CHARS",
            1500,
        ),
        max_output_chars=_read_int_optional(
            read_env_var_optional,
            "LLM_MAX_OUTPUT_CHARS",
            1200,
        ),
    )
    return AppSettings(telegram_auth=telegram_auth, llm=llm)
