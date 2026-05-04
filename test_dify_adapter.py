import unittest
from unittest.mock import MagicMock, patch

from integrations.dify.adapter import (
    DifyChatAdapter,
    _build_empty_answer_error_message,
    _extract_dify_answer,
)


class DifyAdapterTests(unittest.TestCase):
    def test_extract_dify_answer_returns_none_when_field_is_missing(self) -> None:
        self.assertIsNone(_extract_dify_answer({"message": "ok"}))

    def test_build_empty_answer_error_message_mentions_chatflow_answer_node(self) -> None:
        message = _build_empty_answer_error_message(
            {
                "event": "message",
                "mode": "advanced-chat",
                "conversation_id": "conv-123",
            }
        )

        self.assertIn("empty answer", message)
        self.assertIn("Answer node", message)
        self.assertIn("advanced-chat", message)
        self.assertIn("conv-123", message)

    def test_reply_raises_clear_error_when_dify_returns_empty_answer(self) -> None:
        adapter = DifyChatAdapter(api_key="token", base_url="http://dify.local")
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "event": "message",
            "task_id": "task-1",
            "message_id": "message-1",
            "conversation_id": "conv-123",
            "mode": "advanced-chat",
            "answer": "",
            "metadata": {},
            "created_at": 1777933976,
        }

        with patch("integrations.dify.adapter.get_langfuse_client", return_value=None):
            with patch("integrations.dify.adapter.requests.post", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "empty answer"):
                    adapter.reply("user-1", "hello")


if __name__ == "__main__":
    unittest.main()
