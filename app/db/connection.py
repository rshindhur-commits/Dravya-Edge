import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine

    if _engine is not None:
        return _engine

    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    _engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        connect_args={
            "connect_timeout": int(
                os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")
            )
        },
    )

    return _engine