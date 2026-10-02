import logging
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import pymysql
import requests

from . import config, db, nobitex

log = logging.getLogger("hashbot")

HASH_RE = re.compile(r"^[A-Za-z0-9_\-]{8,191}$")


def normalize_hash(raw: str) -> str | None:
    h = (raw or "").strip()
    if h[:2].lower() == "0x":
        h = "0x" + h[2:].lower()
    return h if HASH_RE.match(h) else None


def hash_exists(h: str) -> bool:
    return db.one("SELECT id FROM transactions WHERE tx_hash=%s", (h,)) is not None


# tonapi.io (free, no key) exposes a tx by its own hash, but what users paste
# from a wallet app is very often the *message* hash instead — a different
# value on TON (unlike Ethereum/Tron, where the tx hash is the only hash a
# user ever sees). Try both lookups; whichever answers first wins.
_TONAPI_LOOKUPS = (
    "https://tonapi.io/v2/blockchain/transactions/{h}",
    "https://tonapi.io/v2/blockchain/messages/{h}/transaction",
)


# TRON's first-party free node (api.trongrid.io) answers gettransactionbyid
# without a key at a low rate limit; visible=true asks it to return
# addresses in base58 instead of raw hex, which we need for a readable
# "from/to" and to recognize the USDT-TRC20 contract address below.
_TRONGRID_TX_URL = "https://api.trongrid.io/wallet/gettransactionbyid"
_USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
_ERC20_TRANSFER_SELECTOR = "a9059cbb"


def detect_trx_tx(h: str) -> dict | None:
    """مثل detect_ton_tx ولی برای شبکه‌ی ترون، از طریق TronGrid (بدون کلید):
    هم انتقال خالص TRX را می‌فهمد، هم انتقال USDT (TRC-20) را (از روی
    فراخوانی استاندارد transfer(address,uint256) دیکد می‌شود — توکن‌های
    TRC-20 دیگر پشتیبانی نمی‌شوند تا مبلغ/واحد غلط حدس زده نشود)."""
    try:
        r = requests.post(_TRONGRID_TX_URL, json={"value": h, "visible": True}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        contract = (data.get("raw_data") or {}).get("contract") or []
        if not contract:
            return None
        c = contract[0]
        val = (c.get("parameter") or {}).get("value") or {}
        ctype = c.get("type")

        if ctype == "TransferContract":
            amount_sun = val.get("amount")
            if amount_sun is None:
                return None
            amount = (Decimal(str(amount_sun)) / Decimal(10**6)).quantize(Decimal("0.000000000001"))
            if amount <= 0:
                return None
            return {"network": "TRON", "currency": "TRX", "amount": amount,
                    "from": val.get("owner_address"), "to": val.get("to_address")}

        if ctype == "TriggerSmartContract" and val.get("contract_address") == _USDT_TRC20_CONTRACT:
            call_data = val.get("data") or ""
            if not call_data.startswith(_ERC20_TRANSFER_SELECTOR) or len(call_data) < 8 + 64 + 64:
                return None
            recipient_hex = call_data[8 + 24:8 + 64]
            amount_hex = call_data[8 + 64:8 + 128]
            amount = (Decimal(int(amount_hex, 16)) / Decimal(10**6)).quantize(Decimal("0.000000000001"))
            if amount <= 0:
                return None
            return {"network": "TRON", "currency": "USDT", "amount": amount,
                    "from": val.get("owner_address"), "to": f"0x41{recipient_hex} (hex)"}
        return None
    except Exception as e:
        log.warning("trongrid lookup failed: %s", e)
        return None


def detect_tx(h: str) -> dict | None:
    """تلاش برای شناسایی خودکار شبکه/ارز/مقدار یک Hash، اول TON سپس TRON؛
    اگر هیچ‌کدام جواب ندادند None (یعنی ورود دستی لازم است)."""
    return detect_ton_tx(h) or detect_trx_tx(h)


def detect_ton_tx(h: str) -> dict | None:
    """بالقوه شبکه/ارز/مقدار/فرستنده/گیرنده را از روی Hash تراکنش TON حدس می‌زند
    (بدون نیاز به API Key، از tonapi.io). اگر چیزی پیدا/فهمیده نشود None
    برمی‌گرداند — هیچ‌وقت استثنا پرتاب نمی‌کند، تا جریان ورود دستی همیشه
    به‌عنوان جایگزین کار کند و هرگز مبلغ حدسی/نادرست ثبت نشود."""
    for pattern in _TONAPI_LOOKUPS:
        try:
            r = requests.get(pattern.format(h=h), timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            in_msg = data.get("in_msg") or {}
            value = in_msg.get("value")
            if value is None:
                continue
            amount = (Decimal(str(value)) / Decimal(10**9)).quantize(Decimal("0.000000000001"))
            if amount <= 0:
                continue
            src, dst = in_msg.get("source"), in_msg.get("destination")
            return {
                "network": "TON", "currency": "TON", "amount": amount,
                "from": src.get("address") if isinstance(src, dict) else src,
                "to": dst.get("address") if isinstance(dst, dict) else dst,
            }
        except Exception as e:
            log.warning("tonapi lookup failed for %s: %s", pattern, e)
            continue
    return None


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
