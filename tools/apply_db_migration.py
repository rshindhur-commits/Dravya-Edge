from pathlib import Path
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)
url = os.getenv("DATABASE_DIRECT_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
if not url:
    raise SystemExit("DATABASE_DIRECT_URL or DATABASE_URL is required")
filename = sys.argv[1] if len(sys.argv) > 1 else "001_promote_scanner_artifacts.sql"
script = ROOT / "app" / "db" / "migrations" / filename
sql = "\n".join(
    line for line in script.read_text(encoding="utf-8").splitlines()
    if not line.strip().startswith("--")
)
engine = create_engine(url, pool_pre_ping=True)
with engine.begin() as connection:
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(text(statement))
print(f"Migration {filename} applied successfully.")
