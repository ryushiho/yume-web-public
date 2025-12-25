# app/schema.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _has_column(engine: Engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table});")).fetchall()
    for r in rows:
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        if len(r) >= 2 and r[1] == column:
            return True
    return False


def ensure_sqlite_schema(engine: Engine) -> None:
    """Alembic 없이 최소한의 스키마 보정."""
    # member_users.is_admin
    if not _has_column(engine, "member_users", "is_admin"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE member_users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0;"))

    # app_meta 테이블은 create_all로 생성되지만, 안전망으로 한 번 더
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_meta ("
            "key VARCHAR(100) PRIMARY KEY,"
            "value TEXT,"
            "updated_at DATETIME"
            ");"
        ))
