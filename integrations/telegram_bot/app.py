import asyncio
import json
import logging
from typing import Protocol

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command
from aiogram.types import Message, Update

from integrations.telegram_auth import TelegramAuthConfig, is_admin_user


logger = logging.getLogger(__name__)


class ReplyService(Protocol):
    def reply(self, user_id: int, text: str) -> str:
        ...


class TelegramBotApp:
    def __init__(
        self,
        auth_config: TelegramAuthConfig,
        reply_service: ReplyService | None = None,
    ) -> None:
        self.auth_config = auth_config
        self.reply_service = reply_service
        self.bot = Bot(token=auth_config.bot_token)
        self.dp = Dispatcher()
        self.shutdown_requested = asyncio.Event()
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.dp.message(Command("start"))
        async def handle_start(message: Message) -> None:
            await message.answer(
                "Hi! Send a message and I will reply via LLM."
            )

        @self.dp.message(Command("stop"))
        async def handle_stop(message: Message) -> None:
            if self.auth_config.admin_id is None:
                await message.answer("ADMIN_ID is not configured.")
                return

            requester_id = message.from_user.id if message.from_user else None
            if not is_admin_user(requester_id, self.auth_config.admin_id):
                await message.answer("Access denied.")
                return

            logger.info("Shutdown requested by admin_id=%s", requester_id)
            await message.answer("Stopping bot...")
            self.shutdown_requested.set()
            try:
                await self.dp.stop_polling()
            except RuntimeError:
                # Polling may already be stopping/stopped.
                logger.info("Polling is already stopping")

        @self.dp.message()
        async def catch_all(message: Message) -> None:
            logger.info(
                "Got message: chat_id=%s type=%s text=%r",
                message.chat.id,
                message.content_type,
                message.text,
            )
            if not message.text:
                await message.answer(
                    "I currently support text messages only."
                )
                return

            if self.reply_service is None:
                await message.answer(
                    "LLM is not configured. Add OPENAI_API_KEY to .env"
                )
                return

            user_id = message.from_user.id if message.from_user else message.chat.id
            reply_text = await asyncio.to_thread(
                self.reply_service.reply,
                user_id,
                message.text,
            )
            await message.answer(reply_text)

    async def webhook_handler(self, event: dict, context) -> dict:
        del context
        body = event.get("body", "")
        update_data = json.loads(body) if body else {}

        await self.dp.feed_update(self.bot, Update.model_validate(update_data))
        return {"statusCode": 200, "body": ""}

    async def run_polling(self) -> None:
        # If webhook was configured in another deployment, polling may fail or conflict.
        await self.bot.delete_webhook(drop_pending_updates=False)
        logger.info("Bot started")
        polling_task = asyncio.create_task(self.dp.start_polling(self.bot))
        shutdown_task = asyncio.create_task(self.shutdown_requested.wait())

        done, pending = await asyncio.wait(
            {polling_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_task in done and not polling_task.done():
            logger.info("Stopping polling")
            try:
                await self.dp.stop_polling()
            except RuntimeError:
                logger.info("Polling is already stopping")

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        try:
            await polling_task
        except asyncio.CancelledError:
            logger.info("Bot polling task cancelled")
            raise
        except TelegramConflictError:
            logger.error(
                "Polling conflict: another bot instance is already using getUpdates "
                "for this BOT_TOKEN. Stop the other instance or disable webhook/polling there."
            )
            raise
        finally:
            await self.bot.session.close()
