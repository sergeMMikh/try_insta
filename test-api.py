#!/usr/bin/env python3
"""Тестовый модуль для отправки данных в Dify.

Запускайте вручную:
  python test-api.py --mode workflow --text "Кто такие ёжики?" --author yarush72 --comment_id 12345
  python test-api.py --mode webhook --text "Кто такие ёжики?" --author yarush72 --comment_id 12345
"""

import argparse
import json
import os
from pathlib import Path
from pprint import pprint

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


import os
import requests
from urllib.parse import urlencode


def send_workflow_run(text: str, 
                      author: str, comment_id: str, 
                      platform: str = "instagram") -> dict:
    webhook_url = (
        os.getenv("DIFY_WEBHOOK_URL")
        or "https://trigger.ai-plugin.io/triggers/webhook/_b-cE1UCfm1sMhlDvycpxfxA"
    ).strip()

    params = {
        "text": text,
        "author": author,
        "comment_id": comment_id,
        "platform": platform,
    }

    url = f"{webhook_url}?{urlencode(params)}"

    print("Sending request to Dify webhook...")
    print(url)

    response = requests.post(url, timeout=30)
    response.raise_for_status()

    return response.json()


def send_webhook(text: str, author: str, comment_id: str, platform: str = "instagram") -> dict:
    webhook_url = (os.getenv("DIFY_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        raise RuntimeError("DIFY_WEBHOOK_URL is not configured in .env")

    payload = {
        "text": text,
        "author": author,
        "comment_id": comment_id,
        "platform": platform,
    }

    headers = {
        "Content-Type": "application/json",
    }

    response = requests.post(webhook_url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()

    if response.headers.get("Content-Type", "").startswith("application/json"):
        return response.json()

    return {"status_code": response.status_code, "text": response.text}


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Dify API and webhook payloads")
    parser.add_argument("--mode", choices=["workflow", "webhook"], default="workflow")
    parser.add_argument("--text", default="Тестовый комментарий")
    parser.add_argument("--author", default="test_user")
    parser.add_argument("--comment_id", default="test_comment_123")
    parser.add_argument("--platform", default="instagram")
    args = parser.parse_args()

    print(f"Mode: {args.mode}")
    print(f"Text: {args.text}")
    print(f"Author: {args.author}")
    print(f"Comment ID: {args.comment_id}")
    print(f"Platform: {args.platform}")
    print()

    if args.mode == "workflow":
        result = send_workflow_run(args.text, args.author, args.comment_id, args.platform)
    else:
        result = send_webhook(args.text, args.author, args.comment_id, args.platform)

    print("Result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
