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


def detect_tx(h: str) -> dict | None:
    """تلاش برای شناسایی خودکار شبکه/ارز/مقدار یک Hash، اول TON سپس TRON؛
    اگر هیچ‌کدام جواب ندادند None (یعنی ورود دستی لازم است)."""
    return detect_ton_tx(h) or detect_trx_tx(h)


# TON has no single official REST API the way most chains do; these three
# (in order) are the ones that actually expose a free, no-key, hash-based
# lookup. What a wallet app shows/copies is very often the tx's *message*
# hash, not the transaction hash itself (a TON-specific distinction) — so
# both are tried before giving up on tonapi.io, and toncenter.com (the TON
# Foundation's own API) is tried last as a second independent source.
_TONAPI_TX_URL = "https://tonapi.io/v2/blockchain/transactions/{h}"
_TONAPI_MSG_URL = "https://tonapi.io/v2/blockchain/messages/{h}/transaction"
_TONCENTER_TX_URL = "https://toncenter.com/api/v3/transactions"


def detect_ton_tx(h: str) -> dict | None:
    """شبکه/ارز/مقدار/فرستنده/گیرنده را از روی Hash تراکنش TON حدس می‌زند
    (بدون نیاز به API Key). هیچ‌وقت استثنا پرتاب نمی‌کند — شکست یعنی None،
    تا جریان ورود دستی همیشه جایگزین کار کند و هرگز مبلغ حدسی ثبت نشود."""
    for url in (_TONAPI_TX_URL.format(h=h), _TONAPI_MSG_URL.format(h=h)):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            in_msg = r.json().get("in_msg") or {}
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
            log.warning("tonapi lookup failed for %s: %s", url, e)

    try:
        r = requests.get(_TONCENTER_TX_URL, params={"hash": h, "limit": 1}, timeout=10)
        if r.status_code == 200:
            txs = r.json().get("transactions") or []
            if txs:
                in_msg = txs[0].get("in_msg") or {}
                value = in_msg.get("value")
                if value is not None:
                    amount = (Decimal(str(value)) / Decimal(10**9)).quantize(Decimal("0.000000000001"))
                    if amount > 0:
                        return {
                            "network": "TON", "currency": "TON", "amount": amount,
                            "from": in_msg.get("source"), "to": in_msg.get("destination"),
                        }
    except Exception as e:
        log.warning("toncenter lookup failed: %s", e)
    return None


# apilist.tronscanapi.com (free, no key at low rate) already decodes
# TRC-20 transfers for us (symbol, decimals, from/to) — far more reliable
# than hand-parsing TronGrid's raw contract call data, and it works for
# every TRC-20 token, not just USDT.
_TRONSCAN_TX_URL = "https://apilist.tronscanapi.com/api/transaction-info"


def detect_trx_tx(h: str) -> dict | None:
    """مثل detect_ton_tx ولی برای شبکه‌ی ترون (از طریق Tronscan، بدون کلید):
    هم انتقال خالص TRX را می‌فهمد، هم هر انتقال توکن TRC-20 (از جمله
    USDT) را — نماد و تعداد رقم اعشار مستقیماً از خودِ API می‌آید، حدس زده
    نمی‌شود."""
    try:
        r = requests.get(_TRONSCAN_TX_URL, params={"hash": h}, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not data.get("hash"):
            return None  # "hash not found" comes back as 200 + {}
        if data.get("contractRet") not in (None, "SUCCESS"):
            return None  # failed/reverted on-chain — not a real transfer

        trc20 = data.get("trc20TransferInfo") or []
        if trc20:
            t = trc20[0]
            if t.get("status") != 0:
                return None
            try:
                amount = (Decimal(t["amount_str"]) / Decimal(10 ** int(t["decimals"]))
                          ).quantize(Decimal("0.000000000001"))
            except Exception:
                return None
            if amount <= 0:
                return None
            return {"network": "TRON", "currency": (t.get("symbol") or "TRC20").upper(),
                    "amount": amount, "from": t.get("from_address"), "to": t.get("to_address")}

        amount_sun = (data.get("contractData") or {}).get("amount")
        if amount_sun is None:
            return None
        amount = (Decimal(str(amount_sun)) / Decimal(10**6)).quantize(Decimal("0.000000000001"))
        if amount <= 0:
            return None
        return {"network": "TRON", "currency": "TRX", "amount": amount,
                "from": data.get("ownerAddress"), "to": data.get("toAddress")}
    except Exception as e:
        log.warning("tronscan lookup failed: %s", e)
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
