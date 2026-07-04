from sqlalchemy import text

from app.db.connection import get_engine


def main():
    engine = get_engine()

    with engine.connect() as conn:
        result = conn.execute(text("SELECT now()")).scalar_one()

    print(f"Database connection OK: {result}")


if __name__ == "__main__":
    main()