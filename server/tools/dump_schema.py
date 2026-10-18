"""Print the live columns and indexes of the entries and notes tables.

Usage (from repo root): .venv/bin/python server/tools/dump_schema.py
Reads DATABASE_URL from server/.env. Never prints it.
"""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

TABLES = ("entries", "notes")

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL is not set in server/.env")

try:
    conn = psycopg.connect(url, connect_timeout=10)
except Exception as e:
    # Don't echo the exception text: it can contain connection details.
    sys.exit(f"Could not connect: {type(e).__name__}")

with conn:
    for table in TABLES:
        cols = conn.execute(
            """select column_name, data_type, is_nullable, column_default
               from information_schema.columns
               where table_schema = 'public' and table_name = %s
               order by ordinal_position""",
            (table,),
        ).fetchall()
        print(f"\n== {table} ({len(cols)} columns) ==")
        if not cols:
            print("  (table not found)")
        for name, dtype, nullable, default in cols:
            null = "NULL" if nullable == "YES" else "NOT NULL"
            print(f"  {name:<22} {dtype:<28} {null:<9} default={default}")

        idx = conn.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' and tablename = %s order by indexname",
            (table,),
        ).fetchall()
        print(f"  -- indexes --")
        for name, definition in idx:
            print(f"  {name}: {definition}")
