import os
import sqlite3
import argparse


def list_tables(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print("Таблицы:")
    for (name,) in cur.fetchall():
        print(" -", name)


def show_schema(conn: sqlite3.Connection, table: str) -> None:
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
    row = cur.fetchone()
    if not row:
        print("Таблица не найдена")
        return
    print(row[0])


def head(conn: sqlite3.Connection, table: str, limit: int) -> None:
    cur = conn.execute(f"SELECT * FROM {table} LIMIT {limit}")
    cols = [d[0] for d in cur.description]
    print(" | ".join(cols))
    print("-" * (len(" | ".join(cols))))
    for row in cur.fetchall():
        print(" | ".join(str(row[c]) for c in cols))


def main() -> None:
    parser = argparse.ArgumentParser(description="Инспектор SQLite БД приложения")
    parser.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "app.db"))
    parser.add_argument("--tables", action="store_true", help="Показать список таблиц")
    parser.add_argument("--schema", help="Показать схему таблицы")
    parser.add_argument("--head", help="Вывести первые строки таблицы")
    parser.add_argument("--limit", type=int, default=10, help="Лимит строк для --head")
    args = parser.parse_args()

    db_path = os.path.abspath(args.db)
    conn = sqlite3.connect(db_path)
    try:
        if args.tables:
            list_tables(conn)
        if args.schema:
            show_schema(conn, args.schema)
        if args.head:
            head(conn, args.head, args.limit)
        if not (args.tables or args.schema or args.head):
            print("Укажите одну из опций: --tables | --schema TABLE | --head TABLE [--limit N]")
            print("БД:", db_path)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

