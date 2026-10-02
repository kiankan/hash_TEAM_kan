import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import pymysql

from . import config, db, nobitex

HASH_RE = re.compile(r"^[A-Za-z0-9_\-]{8,191}$")


def normalize_hash(raw: str) -> str | None:
    h = (raw or "").strip()
    if h[:2].lower() == "0x":
        h = "0x" + h[2:].lower()
    return h if HASH_RE.match(h) else None


def hash_exists(h: str) -> bool:
    return db.one("SELECT id FROM transactions WHERE tx_hash=%s", (h,)) is not None


def parse_amount(raw: str) -> Decimal | None:
    try:
        a = Decimal((raw or "").strip().replace(",", ""))
    except InvalidOperation:
        return None
    if not a.is_finite() or a <= 0 or a >= Decimal("1e22"):
        return None
    return a.quantize(Decimal("0.000000000001"))


def fmt(n) -> str:
    return f"{int(n):,}"


def jdate(d) -> str:
    try:
        import jdatetime
        return jdatetime.date.fromgregorian(date=d).strftime("%Y/%m/%d")
    except Exception:
        return d.isoformat()


def add_tx(h, network, currency, amount, kind, desc, by=None):
    """(id, value_irt) برمی‌گرداند؛ برای Hash تکراری (None, None)."""
    rate = nobitex.irt_rate(currency)
    value = (amount * rate).quantize(Decimal(1)) if rate else None
    try:
        tid = db.execute(
            "INSERT INTO transactions (tx_hash,network,currency,amount,kind,description,value_irt,created_by,created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (h, network, currency, amount, kind, desc, value, by, db.now()))
    except pymysql.err.IntegrityError:
        return None, None
    return tid, value


def summary(start, end) -> dict:
    rows = db.q("SELECT id,currency,amount,kind,value_irt FROM transactions"
                " WHERE created_at>=%s AND created_at<%s", (start, end))
    inc = exp = Decimal(0)
    unpriced = 0
    for r in rows:
        v = r["value_irt"]
        if v is None:
            rate = nobitex.irt_rate(r["currency"])
            if not rate:
                unpriced += 1
                continue
            v = (r["amount"] * rate).quantize(Decimal(1))
            db.execute("UPDATE transactions SET value_irt=%s WHERE id=%s", (v, r["id"]))
        if r["kind"] == "income":
            inc += v
        else:
            exp += v
    net = inc - exp
    return dict(income=inc, expense=exp, profit=max(net, 0), loss=max(-net, 0),
                count=len(rows), unpriced=unpriced)


def report_text(title: str, start, end) -> str:
    s = summary(start, end)
    t = (f"📊 {title} — {jdate(start.date())}\n"
         f"💰 درآمد: {fmt(s['income'])} تومان\n"
         f"💸 هزینه: {fmt(s['expense'])} تومان\n"
         f"🟢 سود: {fmt(s['profit'])} تومان\n"
         f"🔴 ضرر: {fmt(s['loss'])} تومان\n"
         f"📥 تراکنش‌ها: {s['count']}")
    if s["unpriced"]:
        t += f"\n⚠️ قیمت {s['unpriced']} تراکنش از نوبیتکس دریافت نشد و در جمع نیامده."
    return t


def create_reminder(chat_id, tx_hash, title, days=7):
    nxt = db.now() + timedelta(days=days)
    rid = db.execute(
        "INSERT INTO reminders (tx_hash,title,interval_days,next_at,chat_id,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (tx_hash[:191], title[:300], days, nxt, chat_id, db.now()))
    return rid, nxt


def recipients() -> set[int]:
    ids = set(config.ADMIN_IDS)
    ids |= {r["telegram_id"] for r in db.q("SELECT telegram_id FROM allowed_users")}
    return ids


def default_chat_id():
    ids = sorted(recipients())
    return ids[0] if ids else None
