import sqlite3

DB_NAME = "search_engine.db"


def get_connection():
    return sqlite3.connect(DB_NAME)


def init_database():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            content TEXT
        )
    """)

    connection.commit()
    connection.close()


if __name__ == "__main__":
    init_database()
    print("Database initialized successfully!")