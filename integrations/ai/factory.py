import logging
from typing import Callable

from .adapter import LLMAdapter
from .config import LLMConfig


logger = logging.getLogger(__name__)


EnvReader = Callable[[str], str]
EnvOptionalReader = Callable[[str, str | None], str | None]


def build_llm_adapter(config: LLMConfig) -> LLMAdapter | None:
    if not config.api_key:
        return None

    return LLMAdapter(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        system_prompt=config.system_prompt,
        memory_size=config.memory_size,
        rate_limit_max_requests=config.rate_limit_max_requests,
        rate_limit_window_seconds=config.rate_limit_window_seconds,
        max_input_chars=config.max_input_chars,
        max_output_chars=config.max_output_chars,
        timeout_seconds=config.timeout_seconds,
    )


def build_llm_adapter_from_env(
    read_env_var: EnvReader,
    read_env_var_optional: EnvOptionalReader,
) -> LLMAdapter | None:
    try:
        api_key = read_env_var("OPENAI_API_KEY")
    except (KeyError, ValueError, FileNotFoundError) as exc:
        logger.warning("OPENAI_API_KEY is not configured: %s", exc)
        return None

    config = LLMConfig(
        api_key=api_key,
        model=read_env_var_optional("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        base_url=(
            read_env_var_optional("OPENAI_BASE_URL", "https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        ),
        system_prompt=read_env_var_optional("LLM_SYSTEM_PROMPT"),
        memory_size=int(read_env_var_optional("LLM_MEMORY_SIZE", "8") or "8"),
        rate_limit_max_requests=int(
            read_env_var_optional("LLM_RATE_LIMIT_MAX_REQUESTS", "5") or "5"
        ),
        rate_limit_window_seconds=int(
            read_env_var_optional("LLM_RATE_LIMIT_WINDOW_SECONDS", "60") or "60"
        ),
        max_input_chars=int(
            read_env_var_optional("LLM_MAX_INPUT_CHARS", "1500") or "1500"
        ),
        max_output_chars=int(
            read_env_var_optional("LLM_MAX_OUTPUT_CHARS", "1200") or "1200"
        ),
    )
    return build_llm_adapter(config)
