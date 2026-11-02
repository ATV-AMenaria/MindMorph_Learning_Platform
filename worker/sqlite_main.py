"""SQLite whole-program adapter: SQL statements produce JSON result rows."""
import json
from pathlib import Path
import sqlite3
import sys

db = sqlite3.connect(":memory:")
statement = ""
for line in Path(sys.argv[1]).read_text().splitlines(keepends=True):
    # Split at complete SQL boundaries, including multiple statements per line.
    for char in line:
        statement += char
        if char == ";" and sqlite3.complete_statement(statement):
            cursor = db.execute(statement)
            if cursor.description:
                print(json.dumps(cursor.fetchall(), separators=(",", ":")))
            statement = ""
if statement.strip():
    cursor = db.execute(statement)
    if cursor.description:
        print(json.dumps(cursor.fetchall(), separators=(",", ":")))
