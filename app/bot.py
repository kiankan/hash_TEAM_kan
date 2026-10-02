import asyncio
import html
import logging
import os
import re
from decimal import Decimal
from datetime import datetime, time as dtime, timedelta
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

from . import config, db, fragment, services, nobitex

log = logging.getLogger("hashbot")
esc = lambda s: html.escape(str(s))


def B(text, data): return InlineKeyboardButton(text, callback_data=data)
MENU_BTN = [B("🏠 منوی اصلی", "menu")]
CANCEL = M([MENU_BTN])


# -------------------- منوی اصلی --------------------
def menu_markup(uid):
    rows = [
        [B("➕ ثبت سریع تراکنش", "add"), B("📜 تاریخچه", "hist:0")],
        [B("📊 گزارش امروز", "rep"), B("🔔 یادآوری‌ها", "rem")],
        [B("💱 قیمت ارز", "prices"), B("👤 Fragment", "frag")],
    ]
    if uid in config.ADMIN_IDS:
        rows.append([B("⚙️ مدیریت ربات", "admin")])
    rows.append(MENU_BTN)
    return M(rows)


def kb(*rows):
    return M([*rows, MENU_BTN])


def allowed(uid):
    return uid in config.ADMIN_IDS or db.one(
        "SELECT 1 x FROM allowed_users WHERE telegram_id=%s", (uid,)
    ) is not None


def guard(fn):
    @wraps(fn)
    async def w(update, ctx):
        u = update.effective_user
        if not u or not allowed(u.id):
            if u:
                db.log_event("warn", f"unauthorized telegram id {u.id}")
            if update.callback_query:
                await update.callback_query.answer()
            return
        return await fn(update, ctx)
    return w


async def reply(update, text, markup=None):
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def show_menu(update):
    await reply(update, "🏠 <b>منوی اصلی</b>", menu_markup(update.effective_user.id))


# -------------------- قیمت نوبیتکس --------------------
PRICE_WORDS = {
    "ton":"TON","تون":"TON","gram":"TON","گرام":"TON","گرم":"TON",
    "trx":"TRX","ترون":"TRX","btc":"BTC","بیتکوین":"BTC","بیت‌کوین":"BTC",
    "eth":"ETH","اتریوم":"ETH","usdt":"USDT","تتر":"USDT",
}
PRICE_RE = re.compile(
    r"^\s*(?:(\d+(?:[.,]\d+)?)\s*)?"
    r"(ton|تون|gram|گرام|گرم|trx|ترون|btc|بیتکوین|بیت‌کوین|eth|اتریوم|usdt|تتر)\s*$",
    re.I,
)


def parse_price_message(text):
    m = PRICE_RE.fullmatch((text or "").strip().replace("٬", ","))
    if not m:
        return None
    amount = Decimal(m.group(1).replace(",", ".")) if m.group(1) else Decimal(1)
    return (PRICE_WORDS[m.group(2).lower()], amount) if amount > 0 else None


async def send_price(update, asset, amount=Decimal(1)):
    usd = await asyncio.to_thread(nobitex.usd_price, asset.lower())
    toman = await asyncio.to_thread(nobitex.irt_rate, asset.lower())
    if usd is None or toman is None:
        await reply(update, "❌ قیمت این ارز در نوبیتکس در دسترس نیست.", CANCEL)
        return
    total_usd, total_irt = usd * amount, toman * amount
    a = format(amount.normalize(), "f")
    await reply(
        update,
        f"💎 <b>{a} {asset}</b>\n\n"
        f"💵 دلار: <b>${nobitex.format_usd(total_usd)}</b>\n"
        f"🇮🇷 تومان: <b>{nobitex.format_toman(total_irt)} تومان</b>\n\n"
        f"▫️ هر {asset}: ${nobitex.format_usd(usd)} | "
        f"{nobitex.format_toman(toman)} تومان",
        kb([B("🔄 بروزرسانی", f"price:{asset}")],
           [B("💱 ارزهای دیگر", "prices")])
    )


# -------------------- ثبت سریع Hash --------------------
def looks_like_hash(text):
    h = services.normalize_hash(text)
    return bool(h and len(h) >= 20)


async def start_hash_flow(update, ctx, raw_hash):
    h = services.normalize_hash(raw_hash)
    if not h:
        return False
    if services.hash_exists(h):
        await reply(update, "⛔ این Hash قبلاً ثبت شده است.", menu_markup(update.effective_user.id))
        return True

    ctx.user_data["flow"] = {"name": "add", "step": "kind", "d": {"hash": h}}
    detected = await asyncio.to_thread(services.detect_tx, h)

    if detected:
        d = ctx.user_data["flow"]["d"]
        d.update({
            "network": detected["network"],
            "currency": detected["currency"],
            "amount": detected["amount"],
            "from": detected.get("from"),
            "to": detected.get("to"),
        })
        frm = detected.get("from") or "-"
        to = detected.get("to") or "-"
        await reply(
            update,
            f"✅ <b>تراکنش خودکار شناسایی شد</b>\n\n"
            f"🌐 شبکه: <b>{esc(detected['network'])}</b>\n"
            f"💱 ارز: <b>{esc(detected['currency'])}</b>\n"
            f"🔢 مقدار: <b>{format(detected['amount'].normalize(),'f')}</b>\n"
            f"📤 از: <code>{esc(frm)}</code>\n"
            f"📥 به: <code>{esc(to)}</code>\n\n"
            f"فقط نوع تراکنش را مشخص کن:",
            kb([B("💰 درآمد", "kind:income"), B("💸 هزینه", "kind:expense")])
        )
        return True

    # فقط در صورت شکست تشخیص، اطلاعات ناقص را می‌پرسیم.
    ctx.user_data["flow"]["step"] = "network"
    await reply(
        update,
        "⚠️ Hash پیدا شد ولی اطلاعات کاملش از شبکه دریافت نشد.\n"
        "فقط شبکه را بفرست؛ بعد فقط مواردی که لازم باشد پرسیده می‌شود.",
        kb(
            [B("TRON", "net:TRON"), B("TON", "net:TON")],
            [B("ETH", "net:ETH"), B("BSC", "net:BSC")],
            [B("SOL", "net:SOL"), B("BTC", "net:BTC")],
        )
    )
    return True


async def add_step(update, ctx, value):
    f = ctx.user_data["flow"]
    d, step, value = f["d"], f["step"], (value or "").strip()

    if step == "network":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
            return await reply(update, "❌ شبکه نامعتبر است.")
        d["network"] = value.upper()
        f["step"] = "currency"
        return await reply(
            update, "💱 ارز را بفرست:",
            kb([B("USDT","cur:USDT"), B("TRX","cur:TRX"), B("TON","cur:TON")],
               [B("BTC","cur:BTC"), B("ETH","cur:ETH")])
        )

    if step == "currency":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", value):
            return await reply(update, "❌ ارز نامعتبر است.")
        d["currency"] = value.upper()
        f["step"] = "amount"
        return await reply(update, "🔢 مقدار را بفرست:")

    if step == "amount":
        a = services.parse_amount(value)
        if a is None:
            return await reply(update, "❌ مقدار نامعتبر است.")
        d["amount"] = a
        f["step"] = "kind"
        return await reply(update, "نوع تراکنش:", kb(
            [B("💰 درآمد","kind:income"), B("💸 هزینه","kind:expense")]
        ))

    if step == "kind":
        if value not in ("income", "expense"):
            return await reply(update, "یکی از دو دکمه را انتخاب کن.")
        d["kind"] = value
        f["step"] = "desc"
        return await reply(update, "📝 توضیح؟ اگر نمی‌خواهی، «-» بفرست.")

    if step == "desc":
        desc = "" if value == "-" else value[:500]
        tid, irt = await asyncio.to_thread(
            services.add_tx, d["hash"], d["network"], d["currency"],
            d["amount"], d["kind"], desc, update.effective_user.id
        )
        ctx.user_data.clear()
        if not tid:
            return await reply(update, "⛔ این Hash قبلاً ثبت شده است.", menu_markup(update.effective_user.id))
        price = f"\n💰 ارزش تقریبی: {services.fmt(irt)} تومان" if irt is not None else ""
        return await reply(
            update, f"✅ <b>تراکنش #{tid} ثبت شد.</b>{price}",
            menu_markup(update.effective_user.id)
        )


# -------------------- مدیریت داخل خود ربات --------------------
async def admin_menu(update):
    await reply(
        update,
        "⚙️ <b>مدیریت ربات</b>",
        kb(
            [B("💱 تنظیم نوبیتکس", "nobitex"), B("👥 کاربران مجاز", "users")],
            [B("📊 وضعیت", "status")],
        )
    )


async def admin_users(update):
    rows = db.q("SELECT telegram_id,name,created_at FROM allowed_users ORDER BY created_at DESC LIMIT 50")
    body = "👥 <b>کاربران مجاز</b>\n\n"
    if not rows:
        body += "هیچ کاربری ثبت نشده."
    else:
        body += "\n".join(
            f"• <code>{r['telegram_id']}</code> — {esc(r['name']) or 'بدون نام'}"
            for r in rows
        )
    await reply(
        update, body,
        kb([B("➕ افزودن کاربر", "user_add")],
           [B("➖ حذف کاربر", "user_del")])
    )


async def admin_nobitex(update):
    status = nobitex.credentials_status()
    await reply(
        update,
        "💱 <b>تنظیم نوبیتکس</b>\n\n"
        f"API Key: {'✅ تنظیم شده' if status['key'] else '❌ تنظیم نشده'}\n"
        f"Secret Key: {'✅ تنظیم شده' if status['secret'] else '❌ تنظیم نشده'}\n\n"
        "برای تنظیم، هر دو مقدار را پشت‌سرهم بفرست:\n"
        "<code>API_KEY\nSECRET_KEY</code>",
        kb([B("🗑 حذف کلیدها", "nobitex_clear")])
    )


# -------------------- متن --------------------
@guard
async def on_text(update, ctx):
    f = ctx.user_data.get("flow")
    text = (update.message.text or "").strip()

    # جریان‌های داخلی
    if f:
        name = f["name"]
        if name == "add_wait":
            ctx.user_data.clear()
            return await start_hash_flow(update, ctx, text)
        if name == "add":
            return await add_step(update, ctx, text)
        if name == "nobitex":
            lines = text.splitlines()
            if len(lines) != 2 or not all(lines):
                return await reply(update, "❌ دقیقاً دو خط بفرست: API Key و Secret Key")
            nobitex.save_credentials(lines[0].strip(), lines[1].strip())
            ctx.user_data.clear()
            return await reply(update, "✅ کلیدهای نوبیتکس ذخیره شدند.", menu_markup(update.effective_user.id))
        if name == "user_add":
            if not re.fullmatch(r"\d{4,15}", text):
                return await reply(update, "❌ Telegram ID نامعتبر است.")
            ctx.user_data["user_add_id"] = int(text)
            ctx.user_data["flow"]["step"] = "name"
            return await reply(update, "نام کاربر را بفرست:")
        if name == "user_del":
            if not re.fullmatch(r"\d{4,15}", text):
                return await reply(update, "❌ Telegram ID نامعتبر است.")
            db.execute("DELETE FROM allowed_users WHERE telegram_id=%s", (int(text),))
            ctx.user_data.clear()
            return await reply(update, "✅ کاربر حذف شد.", menu_markup(update.effective_user.id))
        if name == "user_add" and f.get("step") == "name":
            pass

    # ادامه افزودن کاربر
    if f and f["name"] == "user_add" and f.get("step") == "name":
        tid = ctx.user_data.pop("user_add_id")
        db.execute(
            "INSERT IGNORE INTO allowed_users (telegram_id,name,created_at) VALUES (%s,%s,%s)",
            (tid, text[:100], db.now())
        )
        ctx.user_data.clear()
        return await reply(update, "✅ کاربر اضافه شد.", menu_markup(update.effective_user.id))

    # ارسال مستقیم Hash
    if looks_like_hash(text):
        if await start_hash_flow(update, ctx, text):
            return

    # قیمت کوتاه
    p = parse_price_message(text)
    if p:
        return await send_price(update, *p)

    await show_menu(update)


# -------------------- callback --------------------
@guard
async def on_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    cmd, _, arg = q.data.partition(":")

    if cmd == "menu":
        ctx.user_data.clear()
        return await show_menu(update)

    if cmd == "admin":
        return await admin_menu(update)

    if cmd == "nobitex":
        if update.effective_user.id in config.ADMIN_IDS:
            ctx.user_data["flow"] = {"name":"nobitex","step":"keys","d":{}}
            return await admin_nobitex(update)

    if cmd == "nobitex_clear":
        if update.effective_user.id in config.ADMIN_IDS:
            nobitex.clear_credentials()
            return await reply(update, "🗑 کلیدهای نوبیتکس حذف شدند.", menu_markup(update.effective_user.id))

    if cmd == "users":
        return await admin_users(update)

    if cmd == "user_add":
        ctx.user_data["flow"] = {"name":"user_add","step":"id","d":{}}
        return await reply(update, "🆔 Telegram ID کاربر را بفرست:")

    if cmd == "user_del":
        ctx.user_data["flow"] = {"name":"user_del","step":"id","d":{}}
        return await reply(update, "🆔 Telegram ID کاربر را بفرست:")

    if cmd == "status":
        return await reply(
            update,
            f"⚙️ وضعیت ربات\n\n"
            f"👤 ادمین‌ها: {len(config.ADMIN_IDS)}\n"
            f"👥 کاربران مجاز: {db.one('SELECT COUNT(*) c FROM allowed_users')['c']}\n"
            f"💱 نوبیتکس: {'✅' if nobitex.credentials_status()['key'] else '⚠️ بدون کلید'}",
            menu_markup(update.effective_user.id)
        )

    if cmd == "prices":
        return await reply(
            update, "💱 <b>قیمت لحظه‌ای</b>",
            kb([B("TON","price:TON"),B("TRX","price:TRX")],
               [B("BTC","price:BTC"),B("ETH","price:ETH")],
               [B("USDT","price:USDT")])
        )

    if cmd == "price":
        return await send_price(update, arg.upper())

    if cmd == "add":
        ctx.user_data.clear()
        await reply(update, "🔗 فقط Hash را بفرست؛ بقیه را خودکار تشخیص می‌دهم.")
        ctx.user_data["flow"] = {"name":"add_wait","step":"hash","d":{}}
        return

    if cmd == "hist":
        off = max(int(arg or 0), 0)
        rows = db.q("SELECT * FROM transactions ORDER BY id DESC LIMIT 6 OFFSET %s", (off,))
        if not rows:
            return await reply(update, "تراکنشی یافت نشد.", menu_markup(update.effective_user.id))
        nav=[]
        if off: nav.append(B("⬅️ جدیدتر", f"hist:{max(0,off-5)}"))
        if len(rows)>5: nav.append(B("قدیمی‌تر ➡️", f"hist:{off+5}"))
        return await reply(update, "\n\n".join(fmt_tx(r, True) for r in rows[:5]), kb(nav) if nav else CANCEL)

    if cmd == "rep":
        t0=db.now().replace(hour=0,minute=0,second=0,microsecond=0)
        return await reply(update, await asyncio.to_thread(services.report_text,"گزارش امروز",t0,db.now()+timedelta(seconds=1)), CANCEL)

    if cmd == "rem":
        rows=db.q("SELECT * FROM reminders WHERE active=1 ORDER BY next_at LIMIT 10")
        return await reply(update, f"🔔 یادآوری‌های فعال: {len(rows)}", kb([B("➕ یادآوری جدید","remnew")]))

    if cmd == "remnew":
        ctx.user_data["flow"]={"name":"remnew","step":"hash","d":{}}
        return await reply(update,"🔗 Hash را بفرست:")

    if cmd == "frag":
        ctx.user_data["flow"]={"name":"frag","step":"url","d":{}}
        return await reply(update,"👤 لینک Fragment یا username را بفرست:")

    if cmd in ("net","cur","kind"):
        f=ctx.user_data.get("flow")
        if f and f.get("name")=="add":
            return await add_step(update,ctx,arg)

    if cmd == "stop":
        db.execute("UPDATE reminders SET active=0 WHERE id=%s",(int(arg),))
        return await reply(update,f"⏹ یادآوری #{arg} متوقف شد.",menu_markup(update.effective_user.id))


def fmt_tx(r, short=False):
    e="💰" if r["kind"]=="income" else "💸"
    val=f" ≈ {services.fmt(r['value_irt'])} تومان" if r["value_irt"] is not None else ""
    return f"{e} <b>#{r['id']}</b> {format(r['amount'].normalize(),'f')} {esc(r['currency'])} ({esc(r['network'])}){val}\n<code>{esc(r['tx_hash'])}</code>"


async def start(update, ctx):
    if not allowed(update.effective_user.id):
        return
    ctx.user_data.clear()
    await show_menu(update)


async def remind_job(ctx):
    for r in db.q("SELECT * FROM reminders WHERE active=1 AND next_at<=%s",(db.now(),)):
        nxt=db.now()+timedelta(days=r["interval_days"])
        try:
            await ctx.bot.send_message(
                r["chat_id"],
                f"🔔 <b>یادآوری</b>\nHash: <code>{esc(r['tx_hash'])}</code>\nعنوان: {esc(r['title'])}",
                parse_mode=ParseMode.HTML,
                reply_markup=M([[B("⏹ توقف",f"stop:{r['id']}")]])
            )
            db.execute("UPDATE reminders SET next_at=%s,send_count=send_count+1 WHERE id=%s",(nxt,r["id"]))
        except Exception as e:
            db.log_event("error",repr(e))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است.")
    app=Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start","menu"],start))
    app.add_handler(CommandHandler("admin",admin_menu))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_text))
    app.add_error_handler(lambda u,c: log.error("handler error",exc_info=c.error))
    app.job_queue.run_repeating(remind_job, interval=60, first=15)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__=="__main__":
    main()
