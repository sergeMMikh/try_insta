import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("META_ACCESS_TOKEN")
VER = os.getenv("GRAPH_VERSION", "v19.0")

if not TOKEN:
    print("Нет META_ACCESS_TOKEN в .env")
    sys.exit(1)

BASE = f"https://graph.facebook.com/{VER}"


def gget(path: str, params: dict | None = None) -> dict:
    if params is None:
        params = {}
    params["access_token"] = TOKEN
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    try:
        data = r.json()
    except Exception:
        print("Не смог распарсить JSON:", r.text)
        raise
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"API error: {data}")
    return data


def main():
    # 1) Получаем Facebook Pages, которыми ты управляешь
    pages = gget("/me/accounts", params={"fields": "id,name"})
    if "data" not in pages or not pages["data"]:
        print("Не нашёл Pages. Нужна Facebook Page и права pages_show_list.")
        return

    page = pages["data"][0]
    page_id = page["id"]
    print(f"✅ Page: {page['name']} ({page_id})")

    # 2) Получаем привязанный Instagram business account
    page_info = gget(f"/{page_id}", params={"fields": "instagram_business_account"})
    iba = page_info.get("instagram_business_account")
    if not iba or "id" not in iba:
        print("❌ У этой Page нет привязанного Instagram Business/Creator аккаунта.")
        print("Нужно привязать IG к Page в настройках проф. аккаунта.")
        return

    ig_user_id = iba["id"]
    print(f"✅ IG User ID: {ig_user_id}")

    # 3) Получаем список медиа (в т.ч. Reels/видео)
    media = gget(
        f"/{ig_user_id}/media",
        params={
            "fields": "id,caption,media_type,media_url,permalink,timestamp,thumbnail_url",
            "limit": 25,
        },
    )

    items = media.get("data", [])
    if not items:
        print("❌ Нет медиа или нет прав instagram_basic.")
        return

    # Берём первое REELS/VIDEO, иначе просто первое
    pick = None
    for it in items:
        if it.get("media_type") in ("REELS", "VIDEO"):
            pick = it
            break
    if not pick:
        pick = items[0]

    print("\n🎬 Нашёл медиа:")
    print("media_id   :", pick.get("id"))
    print("type       :", pick.get("media_type"))
    print("permalink  :", pick.get("permalink"))
    print("timestamp  :", pick.get("timestamp"))
    cap = (pick.get("caption") or "").strip()
    print("caption    :", (cap[:160] + "…") if len(cap) > 160 else cap)

    # Если хочешь — можно дополнительно запросить детальнее по конкретному media_id
    media_id = pick["id"]
    details = gget(
        f"/{media_id}",
        params={
            "fields": "id,media_type,media_url,permalink,caption,timestamp,thumbnail_url"
        },
    )
    print("\n📎 details.media_url:", details.get("media_url"))


if __name__ == "__main__":
    main()
