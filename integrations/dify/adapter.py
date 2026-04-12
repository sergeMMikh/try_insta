import logging
import os
from typing import Any
from pprint import pprint

import requests

logger = logging.getLogger(__name__)


class DifyUserFacingError(Exception):
    pass


def ask_dify_reply(
    text: str,
    author: str,
    comment_id: str,
    platform: str = "instagram",
) -> str:
    api_url = (
        (os.getenv("DIFY_API_URL") or "").strip()
        or "https://api.dify.ai/v1/workflows/run"
    )
    api_key = (os.getenv("DIFY_API_KEY") or "").strip()
    timeout_seconds = _read_timeout_seconds()

    if not api_key:
        raise DifyUserFacingError("DIFY_API_KEY is not configured")

    payload = {
        "inputs": {
            "text": text,
            "author": author,
            "comment_id": comment_id,
            "platform": platform,
        },
        "response_mode": "blocking",
        "user": f"{platform}:{author or comment_id}",
    }

    try:
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise DifyUserFacingError("Dify timed out while generating reply") from exc
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        status_code = exc.response.status_code if exc.response is not None else "?"
        logger.error(
            "Dify workflow HTTP error: status=%s body=%s",
            status_code,
            body,
        )
        raise DifyUserFacingError("Dify workflow request failed") from exc
    except requests.exceptions.RequestException as exc:
        logger.error("Dify workflow network error: %s", exc)
        raise DifyUserFacingError("Could not reach Dify workflow") from exc

    data = response.json()
    print("Dify response:") 
    pprint(data)  # Debug output
    reply = extract_reply(data)
    if not reply:
        print("❌ No reply from Dify:") 
        pprint(data)
        logger.error("Dify workflow response did not contain text: %r", data)
        raise DifyUserFacingError("Dify workflow returned an empty reply")
    else:
        print("✅ Reply:", reply)
        reply_text = reply

    return " ".join(reply_text.split())[:1000]


def send_comment_to_dify(
    platform: str,
    text: str,
    author: str,
    comment_id: str,
) -> bool:
    """
    Send comment data to Dify webhook for processing.

    Args:
        platform: Platform name (e.g., "instagram")
        text: Comment text content
        author: Comment author/username
        comment_id: Unique comment identifier

    Returns:
        True if successful, False otherwise
    """
    webhook_url = (os.getenv("DIFY_WEBHOOK_URL") or "").strip()

    if not webhook_url:
        logger.error("DIFY_WEBHOOK_URL not configured")
        return False

    payload = {
        "platform": platform,
        "text": text,
        "author": author,
        "comment_id": comment_id,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        logger.info(
            "Comment sent to Dify webhook: %s comment_id=%s status=%s",
            webhook_url,
            comment_id,
            response.status_code,
        )
        logger.debug("Dify webhook response: %s", response.text)
        return True
    except requests.exceptions.RequestException as e:
        logger.error(
            "Failed to send comment to Dify webhook: %s url=%s payload=%s",
            e,
            webhook_url,
            payload,
        )
        return False


def extract_reply(data):
    outputs = data.get("data", {}).get("outputs", {})

    # приоритет
    if "text" in outputs:
        return outputs["text"]

    if "LLM_text" in outputs:
        return outputs["LLM_text"]

    # fallback
    return None


def _read_timeout_seconds() -> int:
    raw_value = (os.getenv("DIFY_TIMEOUT_SECONDS") or "").strip() or "30"
    try:
        return max(5, int(raw_value))
    except ValueError:
        return 30
