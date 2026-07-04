from pathlib import Path
import sys

from dotenv import load_dotenv
from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env", override=True)

from app.db.connection import get_engine


def main():
    engine = get_engine()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO scanner_runs (
                    run_id,
                    status,
                    rows_count,
                    payload
                )
                VALUES (
                    :run_id,
                    :status,
                    :rows_count,
                    CAST(:payload AS JSONB)
                )
                ON CONFLICT (run_id)
                DO NOTHING
                """
            ),
            {
                "run_id": "manual_db_test_001",
                "status": "TEST",
                "rows_count": 0,
                "payload": '{"source": "manual_test"}',
            },
        )

    print("Manual DB insert OK")


if __name__ == "__main__":
    main()