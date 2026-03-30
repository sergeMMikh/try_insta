import asyncio
import logging

from app_settings import load_app_settings
from config import read_env_var, read_env_var_optional
from integrations.ai import build_llm_adapter
from integrations.telegram_bot import TelegramBotApp


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

app_settings = load_app_settings(read_env_var, read_env_var_optional)
llm_adapter = build_llm_adapter(app_settings.llm)
telegram_app = TelegramBotApp(app_settings.telegram_auth, llm_adapter)


async def handler(event: dict, context):
    return await telegram_app.webhook_handler(event, context)


async def main():
    await telegram_app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
