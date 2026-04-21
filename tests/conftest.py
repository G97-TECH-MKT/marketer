"""Test-wide setup.

Forces NullPool for DB integration tests to avoid cross-loop asyncpg connection
reuse when FastAPI TestClient spins up multiple request loops. Prod-side pool
behavior is unaffected.
"""

from __future__ import annotations

import os

os.environ.setdefault("DB_USE_NULL_POOL", "true")
