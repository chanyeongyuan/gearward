import os
import pathlib
import psycopg

def run():
    db_url = os.environ["DATABASE_URL"]
    schema = pathlib.Path(__file__).parent / "schema.sql"
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(schema.read_text())
    print("Migration applied.")

if __name__ == "__main__":
    run()
