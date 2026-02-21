import os

from sqlalchemy import create_engine, inspect, text
from dotenv import load_dotenv

# ===== CONFIG =====
load_dotenv()

DATABASE_URL = os.getenv("DABASE_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Set DABASE_URL (or DATABASE_URL) in .env")
TABLE_NAME = "media"  # Change if needed.
SAMPLE_SIZE = 100

# ===== CONNECT =====
engine = create_engine(DATABASE_URL)


def ensure_table_exists() -> None:
    inspector = inspect(engine)
    if inspector.has_table(TABLE_NAME):
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id BIGSERIAL PRIMARY KEY,
                    media_id VARCHAR(64),
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
    print(f"Created missing table: {TABLE_NAME}")


def fetch_random_sample():
    query = text(
        f"""
        SELECT *
        FROM {TABLE_NAME}
        ORDER BY RANDOM()
        LIMIT :limit
        """
    )

    with engine.connect() as conn:
        result = conn.execute(query, {"limit": SAMPLE_SIZE})
        rows = result.fetchall()
        columns = result.keys()

    return rows, columns


def analyze_sample(rows, columns) -> None:
    total = len(rows)

    if total == 0:
        print("No data.")
        return

    col_index = {col: i for i, col in enumerate(columns)}

    views_present = 0
    likes_present = 0
    comments_present = 0

    for row in rows:
        if "views" in col_index and row[col_index["views"]] is not None:
            views_present += 1

        if "likes" in col_index and row[col_index["likes"]] is not None:
            likes_present += 1

        if "comments" in col_index and row[col_index["comments"]] is not None:
            comments_present += 1

    print(f"Total rows: {total}")
    print(f"Views present: {views_present} ({views_present / total:.2%})")
    print(f"Likes present: {likes_present} ({likes_present / total:.2%})")
    print(f"Comments present: {comments_present} ({comments_present / total:.2%})")


if __name__ == "__main__":
    ensure_table_exists()
    rows, columns = fetch_random_sample()
    analyze_sample(rows, columns)
