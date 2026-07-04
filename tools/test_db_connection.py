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

    with engine.connect() as conn:
        result = conn.execute(text("SELECT now()")).scalar_one()

    print(f"Database connection OK: {result}")


if __name__ == "__main__":
    main()