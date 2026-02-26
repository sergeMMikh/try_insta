import os
import requests
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("META_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID", "17841448609549086")

GRAPH = "https://graph.facebook.com/v25.0"


def api_get(
    path: str, token: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if params is None:
        params = {}
    params["access_token"] = token
    url = f"{GRAPH}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=30)
    try:
        data = r.json()
    except Exception:
        r.raise_for_status()
        raise
    if "error" in data:
        raise RuntimeError(f"Graph API error: {data['error']}")
    return data


def get_latest_media_id(ig_user_id: str, token: str) -> Optional[str]:
    fields = (
        "id,media_type,timestamp,permalink,caption,media_url,like_count,comments_count"
    )
    data = api_get(
        f"{ig_user_id}/media",
        token,
        params={"fields": fields, "limit": 5},
    )
    items: List[Dict[str, Any]] = data.get("data", [])
    if not items:
        return None
    # Берём самое свежее
    return items[0]["id"]


def get_media_details(media_id: str, token: str) -> Dict[str, Any]:
    fields = "id,media_type,media_url,permalink,thumbnail_url,caption,timestamp,like_count,comments_count"
    return api_get(media_id, token, params={"fields": fields})


def get_media_insights(media_id: str, token: str) -> Dict[str, Any]:
    # Для Reels/Video набор метрик зависит от типа и версии API.
    # Попробуем наиболее типичные; если часть не поддерживается — Graph вернёт ошибку.
    metrics = "plays,reach,impressions,saved,video_views"
    return api_get(f"{media_id}/insights", token, params={"metric": metrics})


def main():
    token = os.getenv("META_TOKEN")  # положи токен в переменную окружения
    ig_user_id = os.getenv("IG_USER_ID", "17841448609549086")

    if not token:
        raise SystemExit("Set META_TOKEN env var with a valid User Access Token.")

    media_id = get_latest_media_id(ig_user_id, token)
    if not media_id:
        print("В аккаунте нет медиа.")
        return

    print(f"Latest MEDIA_ID: {media_id}")

    details = get_media_details(media_id, token)
    print("Details:", details)

    # инсайты — опционально
    try:
        insights = get_media_insights(media_id, token)
        print("Insights:", insights)
    except RuntimeError as e:
        print("Insights not available for this media or metrics mismatch:", e)


if __name__ == "__main__":
    main()
