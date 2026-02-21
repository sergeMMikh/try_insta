import os
import subprocess
import sys
from datetime import datetime, timezone


def _maybe_reexec_in_venv() -> bool:
    venv_python = os.path.join(
        os.path.dirname(__file__), ".venv", "Scripts", "python.exe"
    )
    if not os.path.exists(venv_python):
        return False

    if os.path.abspath(sys.executable).lower() == os.path.abspath(venv_python).lower():
        return False

    exit_code = subprocess.call([venv_python, __file__, *sys.argv[1:]])
    raise SystemExit(exit_code)


try:
    import requests
except ImportError as exc:
    if _maybe_reexec_in_venv():
        raise SystemExit(0)
    raise SystemExit("Missing dependency: requests. Install requirements.") from exc

try:
    from sqlalchemy import create_engine, inspect, text
except ImportError as exc:
    if _maybe_reexec_in_venv():
        raise SystemExit(0)
    raise SystemExit("Missing dependency: SQLAlchemy. Install requirements.") from exc

try:
    from dotenv import load_dotenv
except ImportError:
    # Allows running even if python-dotenv is not installed.
    def load_dotenv():
        return False


load_dotenv()

DATABASE_URL = os.getenv("DABASE_URL") or os.getenv("DATABASE_URL")
META_TOKEN = os.getenv("META_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")

if not DATABASE_URL:
    raise SystemExit("DABASE_URL (or DATABASE_URL) not set")
if not META_TOKEN:
    raise SystemExit("META_TOKEN not set")
if not IG_USER_ID:
    raise SystemExit("IG_USER_ID not set")

GRAPH_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

TABLE_NAME = "media"
LIMIT = 100

engine = create_engine(DATABASE_URL)


def ensure_table_exists():
    inspector = inspect(engine)
    if inspector.has_table(TABLE_NAME):
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,
                    media_id VARCHAR(64) UNIQUE,
                    account_id VARCHAR(64),
                    media_type VARCHAR(64),
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    published_at TIMESTAMPTZ,
                    fetched_at TIMESTAMPTZ
                )
                """
            )
        )
    print("Table created.")


def ensure_upsert_index():
    # ON CONFLICT (media_id) requires a unique/exclusion constraint or index.
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                DELETE FROM {TABLE_NAME} a
                USING {TABLE_NAME} b
                WHERE a.ctid < b.ctid
                  AND a.media_id IS NOT NULL
                  AND a.media_id = b.media_id
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {TABLE_NAME}_media_id_uq_idx
                ON {TABLE_NAME} (media_id)
                """
            )
        )


class GraphAPIError(RuntimeError):
    def __init__(self, error: dict):
        self.error = error
        message = error.get("message") or str(error)
        super().__init__(message)

    @property
    def code(self):
        return self.error.get("code")

    @property
    def subcode(self):
        return self.error.get("error_subcode")


def ig_get(path: str, params: dict):
    params = dict(params)
    params["access_token"] = META_TOKEN
    try:
        r = requests.get(f"{BASE_URL}/{path}", params=params, timeout=30)
        data = r.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Graph API returned non-JSON response.") from exc

    if r.status_code >= 400 or "error" in data:
        error = data.get("error", data)
        raise GraphAPIError(error)

    return data


def fetch_media():
    fields = "id,media_type,like_count,comments_count,timestamp"
    data = ig_get(f"{IG_USER_ID}/media", {"fields": fields, "limit": LIMIT})
    return data.get("data", [])


def fetch_views(media_id):
    try:
        data = ig_get(f"{media_id}/insights", {"metric": "views"})
        rows = data.get("data", [])
        if not rows:
            return None
        values = rows[0].get("values", [])
        if not values:
            return None
        return int(values[0].get("value"))
    except Exception:
        return None


def upsert(row, views):
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                INSERT INTO {TABLE_NAME}
                (media_id, account_id, media_type, views, likes, comments, published_at, fetched_at)
                VALUES
                (:media_id, :account_id, :media_type, :views, :likes, :comments, :published_at, :fetched_at)
                ON CONFLICT (media_id) DO UPDATE SET
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes,
                    comments = EXCLUDED.comments,
                    fetched_at = EXCLUDED.fetched_at
                """
            ),
            {
                "media_id": row["id"],
                "account_id": IG_USER_ID,
                "media_type": row.get("media_type"),
                "views": views,
                "likes": row.get("like_count"),
                "comments": row.get("comments_count"),
                "published_at": row.get("timestamp"),
                "fetched_at": now,
            },
        )


def main():
    ensure_table_exists()
    ensure_upsert_index()

    try:
        media = fetch_media()
    except GraphAPIError as exc:
        if exc.code == 190 and exc.subcode == 463:
            print("META_TOKEN expired. Update META_TOKEN in .env and retry.")
            print(f"Graph message: {exc}")
            return
        print(f"Graph API error: {exc}")
        return

    print(f"Fetched media: {len(media)}")

    inserted = 0
    views_count = 0

    for m in media:
        v = fetch_views(m["id"])
        if v is not None:
            views_count += 1
        upsert(m, v)
        inserted += 1

    print(f"Inserted/updated: {inserted}")
    print(f"Views available: {views_count} / {inserted}")


if __name__ == "__main__":
    main()
