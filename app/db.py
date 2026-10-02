from contextlib import contextmanager
from datetime import datetime
from threading import Lock, local

import pymysql
import pymysql.cursors

from . import config

_state = local()
_reconnect_lock = Lock()


def now() -> datetime:
    """Local application time stored as naive DATETIME, preserving existing DB schema."""
    return datetime.now(config.TZ).replace(tzinfo=None)


def _connect():
    return pymysql.connect(
        **config.DB,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=15,
        write_timeout=15,
    )


def _get_conn():
    c = getattr(_state, "conn", None)
    if c is not None:
        try:
            c.ping(reconnect=False)
            return c
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            _state.conn = None

    with _reconnect_lock:
        c = getattr(_state, "conn", None)
        if c is None:
            c = _connect()
            _state.conn = c
        return c


def _drop_conn():
    c = getattr(_state, "conn", None)
    _state.conn = None
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


@contextmanager
def _cursor():
    c = _get_conn()
    try:
        with c.cursor() as cur:
            yield c, cur
    except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
        _drop_conn()
        raise


def _run(fn):
    try:
        return fn()
    except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
        # One transparent reconnect for dropped/stale connections.
        _drop_conn()
        return fn()


def q(sql, args=None) -> list[dict]:
    def run():
        with _cursor() as (_, cur):
            cur.execute(sql, args)
            return list(cur.fetchall())
    return _run(run)


def one(sql, args=None) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def execute(sql, args=None) -> int:
    def run():
        with _cursor() as (_, cur):
            cur.execute(sql, args)
            return cur.lastrowid
    return _run(run)


def log_event(level: str, message: str) -> None:
    try:
        execute(
            "INSERT INTO event_logs (level, message, created_at) VALUES (%s,%s,%s)",
            (level[:10], str(message)[:1000], now()),
        )
    except Exception:
        # Logging must never break the application.
        pass
