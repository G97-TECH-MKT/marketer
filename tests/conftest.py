"""Test-wide setup.

Unit and async-dispatch tests run with the DB disabled (DATABASE_URL="") so
TestClient lifespan never triggers a real Postgres connection.  This keeps the
non-integration test suite fast regardless of what is in .env.

DB integration tests (test_db_integration.py) re-enable persistence explicitly
using their own fixtures and are run separately (pytest --ignore or -k).

NullPool is still forced so that any test that accidentally enables DB won't
reuse connections across event loops.
"""

from __future__ import annotations

import os

os.environ["DATABASE_URL"] = ""
os.environ.setdefault("DB_USE_NULL_POOL", "true")
