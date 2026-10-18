"""Reproduces the idle-connection bug: Supabase kills a pooled connection, next query must still work.

Run from repo root: .venv/bin/python server/tests/smoke_reconnect.py
"""
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import db  # noqa: E402

db.init()
db._pool.wait()  # the pooled connection is open
with db._pool.connection() as conn:
    # Cut the socket under the open connection, like Supabase's pooler dropping an idle client.
    s = socket.socket(fileno=os.dup(conn.fileno()))
    s.shutdown(socket.SHUT_RDWR)
    s.close()
print("cut the pooled connection's socket, as Supabase's pooler does to idle clients")

try:
    row = db._one("select 1 as ok")
    print("PASS next query still works:", row)
except Exception as e:
    print(f"FAIL next query raised {type(e).__name__}")
    raise SystemExit(1)
finally:
    db.close()
