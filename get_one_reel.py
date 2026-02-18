import os
import sys
import requests
from dotenv import load_dotenv

GRAPH = "https://graph.facebook.com/v25.0"


def gget(path, token, params=None):
    params = params or {}
    params["access_token"] = token
    r = requests.get(f"{GRAPH}/{path.lstrip('/')}", params=params, timeout=30)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def main():
    load_dotenv()

    page_id = os.getenv("FB_PAGE_ID")
    page_token = os.getenv("FB_PAGE_ACCESS_TOKEN")

    if not page_id or not page_token:
        print("Нет FB_PAGE_ID или FB_PAGE_ACCESS_TOKEN в .env")
        sys.exit(1)

    # 1) Получаем IG User ID, привязанный к Page
    page = gget(
        f"{page_id}", page_token, params={"fields": "instagram_business_account"}
    )
    ig = page.get("instagram_business_account", {})
    ig_user_id = ig.get("id")
    if not ig_user_id:
        print(
            "Не найден instagram_business_account. Проверь, что IG профессиональный и привязан к этой Page."
        )
        print("Ответ:", page)
        sys.exit(1)

    print("IG_USER_ID:", ig_user_id)

    # 2) Берём список медиа и ищем первое VIDEO
    media = gget(
        f"{ig_user_id}/media",
        page_token,
        params={
            "fields": "id,media_type,permalink,media_url,thumbnail_url,timestamp,caption",
            "limit": 25,
        },
    )

    items = media.get("data", [])
    video = next((m for m in items if m.get("media_type") == "VIDEO"), None)
    if not video:
        print("Видео не найдено в последних 25 публикациях.")
        print("Первые элементы:", items[:3])
        sys.exit(0)

    print("\n=== ONE VIDEO ===")
    print("id:", video.get("id"))
    print("timestamp:", video.get("timestamp"))
    print("permalink:", video.get("permalink"))
    print("media_url:", video.get("media_url"))
    print("caption:", (video.get("caption") or "")[:120])


if __name__ == "__main__":
    main()
