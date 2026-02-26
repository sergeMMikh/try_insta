from dataclasses import dataclass


@dataclass(slots=True)
class LLMConfig:
    api_key: str | None
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    system_prompt: str | None = None
    memory_size: int = 8
    rate_limit_max_requests: int = 5
    rate_limit_window_seconds: int = 60
    max_input_chars: int = 1500
    max_output_chars: int = 1200
    timeout_seconds: int = 30
