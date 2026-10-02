from contextlib import contextmanager
from datetime import datetime

import pymysql
import pymysql.cursors

from . import config


def now() -> datetime:
    """زمان محلی (بدون tzinfo) برای ذخیره در DATETIME."""
    return datetime.now(config.TZ).replace(tzinfo=None)


@contextmanager
def _conn():
    c = pymysql.connect(
        **config.DB, charset="utf8mb4", autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5,
    )
    try:
        yield c
    finally:
        c.close()


def q(sql, args=None) -> list[dict]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def one(sql, args=None) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def execute(sql, args=None) -> int:
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def log_event(level: str, message: str) -> None:
    try:
        execute("INSERT INTO event_logs (level, message, created_at) VALUES (%s,%s,%s)",
                (level[:10], str(message)[:1000], now()))
    except Exception:
        pass
