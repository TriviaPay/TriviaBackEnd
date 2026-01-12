from sqlalchemy import text

from core.db import engine


def check_letters_table():
    print("Checking letters table...")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM letters"))
        rows = result.fetchall()

        if not rows:
            print("No data found in letters table")
            return

        print(f"Found {len(rows)} rows in letters table:")
        for row in rows:
            print(f"Letter: {row[0]}, Image URL: {row[1]}")


if __name__ == "__main__":
    check_letters_table()
