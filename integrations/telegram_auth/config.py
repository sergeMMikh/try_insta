import logging
from dataclasses import dataclass
from typing import Callable


logger = logging.getLogger(__name__)


EnvReader = Callable[[str], str]


@dataclass(slots=True)
class TelegramAuthConfig:
    bot_token: str
    admin_id: int | None = None


def load_telegram_auth_config(read_env_var: EnvReader) -> TelegramAuthConfig:
    bot_token = read_env_var("BOT_TOKEN")

    try:
        admin_id = int(read_env_var("ADMIN_ID"))
    except (KeyError, ValueError, FileNotFoundError) as exc:
        admin_id = None
        logger.warning("ADMIN_ID is not configured correctly: %s", exc)

    return TelegramAuthConfig(bot_token=bot_token, admin_id=admin_id)


def is_admin_user(user_id: int | None, admin_id: int | None) -> bool:
    if user_id is None or admin_id is None:
        return False
    return int(user_id) == int(admin_id)
