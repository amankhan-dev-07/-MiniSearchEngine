import sqlite3

DB_PATH = "search_engine.db"

connection = sqlite3.connect(DB_PATH)
cursor = connection.cursor()

cursor.execute("""
    UPDATE pages
    SET content = ''
    WHERE url LIKE 'http://127.0.0.1:9000/%'
""")

connection.commit()

print("Old local-page content cleared!")

connection.close()