"""PostgreSQL URL tweaks for SQLAlchemy drivers on AWS RDS and local dev.

- **asyncpg**: SQLAlchemy passes URL query parameters as ``asyncpg.connect()`` kwargs.
  libpq-style ``sslmode=`` is not a valid keyword — use ``ssl=`` (see asyncpg docs).
- **psycopg / libpq**: query parameter ``ssl=`` (often used with asyncpg) is invalid;
  use ``sslmode=`` instead.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def swap_asyncpg_scheme_to_psycopg(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def coerce_plain_postgresql_to_asyncpg_scheme(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def _ssl_query_value_to_sslmode(value: str) -> str:
    v = value.strip().lower()
    if v in ("require", "true", "1", "yes"):
        return "require"
    if v in ("false", "0", "no"):
        return "disable"
    return value


def coerce_libpq_query_for_psycopg(url: str) -> str:
    """Map asyncpg-style ``ssl=`` to ``sslmode=`` for psycopg/libpq."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    has_sslmode = any(k.lower() == "sslmode" for k, _ in pairs)
    out: list[tuple[str, str]] = []
    ssl_consumed = False
    for k, v in pairs:
        kl = k.lower()
        if kl == "sslmode":
            out.append((k, v))
            continue
        if kl == "ssl":
            if not has_sslmode and not ssl_consumed:
                out.append(("sslmode", _ssl_query_value_to_sslmode(v)))
                has_sslmode = True
            ssl_consumed = True
            continue
        out.append((k, v))
    return urlunparse(parsed._replace(query=urlencode(out)))


def coerce_asyncpg_query(url: str) -> str:
    """Turn ``sslmode=`` into ``ssl=`` so asyncpg does not get an invalid kwarg."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    has_ssl = any(k.lower() == "ssl" for k, _ in pairs)
    out: list[tuple[str, str]] = []
    sslmode_val: str | None = None
    for k, v in pairs:
        kl = k.lower()
        if kl == "sslmode":
            if sslmode_val is None:
                sslmode_val = v
            continue
        out.append((k, v))
    if not has_ssl and sslmode_val is not None:
        out.append(("ssl", sslmode_val))
    return urlunparse(parsed._replace(query=urlencode(out)))


def normalize_sync_psycopg_url(url: str) -> str:
    """Alembic and sync scripts: asyncpg URL + libpq-compatible SSL query."""
    return coerce_libpq_query_for_psycopg(swap_asyncpg_scheme_to_psycopg(url))
