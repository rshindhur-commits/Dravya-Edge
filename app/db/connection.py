import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


# Load `.env` here rather than relying on another module having imported first.
# This file only read `os.getenv`, so whether the database worked depended on
# whether something like `app.config.settings` or `app.utils.polygon_client`
# happened to be imported earlier in the process. Entry points that reach the DB
# directly do not import those: `tools/regression_runner.py` imports only
# `app.regression`, so DATABASE_URL was unset, every read failed, and HSR
# reported "No scanner snapshots" for days holding hundreds of archived rows.
# `override=False` keeps real environment and Streamlit Secrets winning, which
# matters on Cloud where there is no `.env` at all.
load_dotenv(override=False)

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