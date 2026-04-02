import os
import requests
import logging

logger = logging.getLogger(__name__)


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
        )
        response.raise_for_status()
        logger.info(f"Comment sent to Dify: {comment_id}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send comment to Dify: {e}")
        return False
