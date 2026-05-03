#!/usr/bin/env python3

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def send_workflow_run(
    text: str,
    author: str,
    comment_id: str,
    platform: str = "instagram",
) -> dict:
    """Send a request to Dify Chatflow API."""
    api_url = (
        (os.getenv("DIFY_API_URL") or "").strip()
        or "https://api.dify.ai/v1/chat-messages"
    )
    api_key = (os.getenv("DIFY_API_KEY") or "").strip()
    timeout_seconds = int((os.getenv("DIFY_TIMEOUT_SECONDS") or "30").strip())

    if not api_key:
        raise RuntimeError("DIFY_API_KEY is not configured in .env")

    payload = {
        "inputs": {
            "text": text,
            "author": author,
            "comment_id": comment_id,
            "platform": platform,
        },
        "query": text,
        "response_mode": "blocking",
        "user": f"{platform}:{author or comment_id}",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print("Sending request to Dify Chatflow API...")
    print(f"URL: {api_url}")
    print("Payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    response = requests.post(
        api_url,
        json=payload,
        headers=headers,
        timeout=timeout_seconds,
    )
    response.raise_for_status()

    if response.headers.get("Content-Type", "").startswith("application/json"):
        return response.json()

    return {"status_code": response.status_code, "text": response.text}


def send_webhook(
    text: str,
    author: str,
    comment_id: str,
    platform: str = "instagram",
) -> dict:
    """Send a request to Dify webhook trigger using query params."""
    webhook_url = (os.getenv("DIFY_WEBHOOK_URL") or "").strip()
    timeout_seconds = int((os.getenv("DIFY_TIMEOUT_SECONDS") or "30").strip())

    if not webhook_url:
        raise RuntimeError("DIFY_WEBHOOK_URL is not configured in .env")

    params = {
        "text": text,
        "author": author,
        "comment_id": comment_id,
        "platform": platform,
    }

    url = f"{webhook_url}?{urlencode(params)}"

    print("Sending request to Dify webhook...")
    print(f"URL: {url}")

    response = requests.post(url, timeout=timeout_seconds)
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

    try:
        if args.mode == "workflow":
            result = send_workflow_run(
                args.text,
                args.author,
                args.comment_id,
                args.platform,
            )
        else:
            result = send_webhook(
                args.text,
                args.author,
                args.comment_id,
                args.platform,
            )
    except requests.HTTPError as exc:
        print("HTTP error:")
        print(f"Status: {exc.response.status_code if exc.response is not None else 'unknown'}")
        print(f"Body: {exc.response.text if exc.response is not None else 'no response body'}")
        raise
    except Exception as exc:
        print(f"Error: {exc}")
        raise

    print("Result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
