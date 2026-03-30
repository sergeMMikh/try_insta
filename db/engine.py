import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


load_dotenv()

_engine: Engine | None = None


def get_database_url() -> str:
    url = os.getenv("DABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DABASE_URL (or DATABASE_URL) is not set")
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            pool_pre_ping=True,
            future=True,
        )
    return _engine
