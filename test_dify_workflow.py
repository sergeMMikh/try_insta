#!/usr/bin/env python3
"""
Тестовый скрипт для проверки Dify workflow.run напрямую.
Запускает ask_dify_reply с тестовыми данными.
"""

import os
import sys
from pathlib import Path

# Добавляем корень проекта в путь
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from integrations.dify.adapter import ask_dify_reply

# Загружаем переменные окружения
load_dotenv()

def test_dify_workflow():
    """Тестируем Dify workflow.run с тестовыми данными."""

    # Тестовые данные
    test_text = "Привет! Расскажите про ваши услуги?"
    test_author = "test_user"
    test_comment_id = "test_123"
    test_platform = "instagram"

    print("🚀 Тестируем Dify workflow.run напрямую...")
    print(f"📝 Текст: {test_text}")
    print(f"👤 Автор: {test_author}")
    print(f"🆔 Comment ID: {test_comment_id}")
    print(f"📱 Платформа: {test_platform}")
    print()

    try:
        reply = ask_dify_reply(
            text=test_text,
            author=test_author,
            comment_id=test_comment_id,
            platform=test_platform,
        )

        print("✅ Успешно получен ответ от Dify:")
        print(f"💬 {reply}")

    except Exception as e:
        print(f"❌ Ошибка при вызове Dify: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_dify_workflow()