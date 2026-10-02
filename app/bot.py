import asyncio
import html
import logging
import re
from datetime import datetime, time as dtime, timedelta
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import config, db, fragment, services

log = logging.getLogger("hashbot")


def B(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


esc = lambda s: html.escape(str(s))
MENU_BTN = [B("🏠 منوی اصلی", "menu")]
CANCEL = M([MENU_BTN])
MENU = M([[B("➕ ثبت تراکنش", "add"), B("📜 تاریخچه", "hist:0")],
          [B("📊 گزارش امروز", "rep"), B("🔔 یادآوری‌ها", "rem")],
          [B("👤 Fragment", "frag")]])


def kb(*rows):
    return M([*rows, MENU_BTN])


PROMPTS = {
    "hash": ("🔗 Hash تراکنش را بفرستید:", CANCEL),
    "network": ("🌐 شبکه را انتخاب یا تایپ کنید:", kb(
        [B(n, f"net:{n}") for n in ("ETH", "BSC", "TRON")], [B(n, f"net:{n}") for n in ("TON", "SOL", "BTC")])),
    "currency": ("💱 ارز را انتخاب یا تایپ کنید:", kb(
        [B(n, f"cur:{n}") for n in ("USDT", "TON", "TRX")], [B(n, f"cur:{n}") for n in ("BTC", "ETH", "IRT")])),
    "amount": ("🔢 مقدار (تعداد واحد ارز) را بفرستید:", CANCEL),
    "kind": ("نوع تراکنش؟", kb([B("💰 درآمد", "kind:income"), B("💸 هزینه", "kind:expense")])),
    "desc": ("📝 توضیحات را بفرستید (برای رد شدن «-» بفرستید):", CANCEL),
    "remind": ("🔔 یادآوری ۷ روزه برای پیگیری این تراکنش فعال شود؟",
               kb([B("✅ بله", "remask:yes"), B("❌ خیر", "remask:no")])),
}
ORDER = ["hash", "network", "currency", "amount", "kind", "desc"]


def allowed(uid: int) -> bool:
    return uid in config.ADMIN_IDS or db.one("SELECT 1 x FROM allowed_users WHERE telegram_id=%s", (uid,)) is not None


def guard(fn):
    @wraps(fn)
    async def w(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if not u or not allowed(u.id):
            if u:
                db.log_event("warn", f"unauthorized telegram id {u.id}")
            if update.callback_query:
                await update.callback_query.answer()
            return
        return await fn(update, ctx)
    return w


async def reply(update: Update, text: str, markup=None):
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def show_menu(update: Update):
    await reply(update, "🏠 منوی اصلی", MENU)


async def ask(update, ctx, step):
    ctx.user_data["flow"]["step"] = step
    text, markup = PROMPTS[step]
    await reply(update, text, markup)


def fmt_tx(r, short=False):
    e = "💰" if r["kind"] == "income" else "💸"
    val = f" ≈ {services.fmt(r['value_irt'])} تومان" if r["value_irt"] is not None else ""
    desc = r["description"][:120] if short else r["description"]
    return (f"{e} <b>#{r['id']}</b> {format(r['amount'].normalize(), 'f')} {esc(r['currency'])} ({esc(r['network'])}){val}\n"
            f"<code>{esc(r['tx_hash'])}</code>\n" + (f"📝 {esc(desc)}\n" if desc else "") +
            f"🕒 {r['created_at']:%Y-%m-%d %H:%M}")


# ---------- جریان ثبت تراکنش ----------
async def add_step(update, ctx, val: str):
    f = ctx.user_data["flow"]
    d, step, val = f["d"], f["step"], val.strip()
    if step == "hash":
        h = services.normalize_hash(val)
        if not h:
            return await reply(update, "❌ Hash نامعتبر است. دوباره بفرستید:", CANCEL)
        if services.hash_exists(h):
            return await reply(update, "⛔ این Hash قبلاً ثبت شده است.", CANCEL)
        d["hash"] = h
    elif step in ("network", "currency"):
        if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,16}", val):
            return await reply(update, "❌ مقدار نامعتبر است. دوباره بفرستید:", PROMPTS[step][1])
        d[step] = val.upper()
    elif step == "amount":
        a = services.parse_amount(val)
        if a is None:
            return await reply(update, "❌ عدد معتبر و مثبت بفرستید:", CANCEL)
        d["amount"] = a
    elif step == "kind":
        if val not in ("income", "expense"):
            return await reply(update, "یکی از دکمه‌ها را بزنید.", PROMPTS["kind"][1])
        d["kind"] = val
    elif step == "desc":
        d["desc"] = "" if val == "-" else val[:500]
        tid, value = await asyncio.to_thread(
            services.add_tx, d["hash"], d["network"], d["currency"], d["amount"], d["kind"], d["desc"],
            update.effective_user.id)
        if tid is None:
            ctx.user_data.clear()
            return await reply(update, "⛔ این Hash قبلاً ثبت شده است.", MENU)
        d["tid"] = tid
        worth = f"\n≈ {services.fmt(value)} تومان" if value is not None else "\n⚠️ قیمت از نوبیتکس دریافت نشد."
        await reply(update, f"✅ ثبت شد (#{tid}){worth}")
        return await ask(update, ctx, "remind")
    elif step == "remind":
        if val == "yes":
            title = d.get("desc") or f"پیگیری تراکنش {d['hash'][:12]}"
            _, nxt = services.create_reminder(update.effective_user.id, d["hash"], title)
            await reply(update, f"🔔 یادآوری تنظیم شد؛ {nxt:%Y-%m-%d %H:%M}")
        ctx.user_data.clear()
        return await show_menu(update)
    await ask(update, ctx, ORDER[ORDER.index(step) + 1])


# ---------- جریان یادآوری ----------
async def rem_step(update, ctx, val: str):
    f = ctx.user_data["flow"]
    d, val = f["d"], val.strip()
    if f["step"] == "hash":
        if not (1 <= len(val) <= 191):
            return await reply(update, "❌ Hash نامعتبر است:", CANCEL)
        d["hash"] = services.normalize_hash(val) or val
        f["step"] = "title"
        return await reply(update, "📝 عنوان یادآوری؟ (مثلاً: پرداخت خرید ID کلکسیونی)", CANCEL)
    rid, nxt = services.create_reminder(update.effective_user.id, d["hash"], val[:300])
    ctx.user_data.clear()
    await reply(update, f"🔔 یادآوری #{rid} ثبت شد.\nاولین اطلاع: {nxt:%Y-%m-%d %H:%M}\n(هر ۷ روز تکرار می‌شود تا متوقفش کنید)", MENU)


async def frag_step(update, ctx, val: str):
    url = fragment.parse_target(val)
    if not url:
        return await reply(update, "❌ لینک یا username معتبر Fragment بفرستید:", CANCEL)
    try:
        i = await asyncio.to_thread(fragment.fetch, url)
    except Exception as e:
        ctx.user_data.clear()
        return await reply(update, f"❌ دریافت اطلاعات ممکن نشد: {esc(type(e).__name__)}", MENU)
    ctx.user_data.clear()
    lines = [f"👤 <b>{esc(i.get('title') or url.rsplit('/', 1)[-1])}</b>"]
    if i.get("status"):
        lines.append(f"📌 وضعیت: {esc(i['status'])}")
    if i.get("price"):
        lines.append(f"💎 قیمت: {esc(i['price'])}")
    lines += [f"▫️ {esc(k)}: {esc(v)}" for k, v in list(i["extra"].items())[:8]]
    if i.get("desc"):
        lines.append(f"ℹ️ {esc(i['desc'][:200])}")
    lines.append(f"🔗 {esc(i['url'])}")
    await reply(update, "\n".join(lines), MENU)


FLOWS = {"add": add_step, "remnew": rem_step, "frag": frag_step}


# ---------- هندلرها ----------
@guard
async def start(update, ctx):
    ctx.user_data.clear()
    await show_menu(update)


@guard
async def on_text(update, ctx):
    f = ctx.user_data.get("flow")
    if not f:
        return await show_menu(update)
    await FLOWS[f["name"]](update, ctx, update.message.text or "")


@guard
async def on_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    cmd, _, arg = q.data.partition(":")
    uid = update.effective_user.id
    if cmd == "menu":
        ctx.user_data.clear()
        await show_menu(update)
    elif cmd == "add":
        ctx.user_data["flow"] = {"name": "add", "step": "hash", "d": {}}
        await ask(update, ctx, "hash")
    elif cmd in ("net", "cur", "kind", "remask") and ctx.user_data.get("flow", {}).get("name") == "add":
        await add_step(update, ctx, arg)
    elif cmd == "remnew":
        ctx.user_data["flow"] = {"name": "remnew", "step": "hash", "d": {}}
        await reply(update, "🔗 Hash مربوط به یادآوری را بفرستید:", CANCEL)
    elif cmd == "frag":
        ctx.user_data["flow"] = {"name": "frag", "step": "url", "d": {}}
        await reply(update, "👤 لینک Fragment یا username را بفرستید:", CANCEL)
    elif cmd == "hist":
        off = max(int(arg or 0), 0)
        rows = db.q("SELECT * FROM transactions ORDER BY id DESC LIMIT 6 OFFSET %s", (off,))
        if not rows:
            return await reply(update, "تراکنشی یافت نشد.", MENU)
        nav = []
        if off:
            nav.append(B("⬅️ جدیدتر", f"hist:{max(off - 5, 0)}"))
        if len(rows) > 5:
            nav.append(B("قدیمی‌تر ➡️", f"hist:{off + 5}"))
        await reply(update, "\n\n".join(fmt_tx(r, True) for r in rows[:5]), kb(nav) if nav else CANCEL)
    elif cmd == "rep":
        t0 = db.now().replace(hour=0, minute=0, second=0, microsecond=0)
        text = await asyncio.to_thread(services.report_text, "گزارش امروز", t0, db.now() + timedelta(seconds=1))
        await reply(update, text, CANCEL)
    elif cmd == "rem":
        rows = db.q("SELECT * FROM reminders WHERE active=1 ORDER BY next_at LIMIT 10")
        await reply(update, f"🔔 یادآوری‌های فعال: {len(rows)}", kb([B("➕ یادآوری جدید", "remnew")]))
        for r in rows:
            await reply(update, f"#{r['id']} — {esc(r['title'])}\n<code>{esc(r['tx_hash'])}</code>\n"
                                f"بعدی: {r['next_at']:%Y-%m-%d %H:%M} | ارسال‌شده: {r['send_count']}",
                        M([[B("⏹ توقف", f"stop:{r['id']}")]]))
    elif cmd == "stop":
        db.execute("UPDATE reminders SET active=0 WHERE id=%s", (int(arg),))
        await q.message.reply_text(f"⏹ یادآوری #{arg} متوقف شد.", reply_markup=CANCEL)


async def remind_job(ctx: ContextTypes.DEFAULT_TYPE):
    for r in db.q("SELECT * FROM reminders WHERE active=1 AND next_at<=%s", (db.now(),)):
        nxt = db.now() + timedelta(days=r["interval_days"])
        try:
            await ctx.bot.send_message(
                r["chat_id"],
                f"🔔 <b>یادآوری</b>\nHash: <code>{esc(r['tx_hash'])}</code>\nعنوان: {esc(r['title'])}\n\n"
                f"برو پیگیری کن، شاید آماده شده باشد.\n(تا {r['interval_days']} روز دیگر دوباره یادآوری می‌شود)",
                parse_mode=ParseMode.HTML, reply_markup=M([[B("⏹ توقف یادآوری", f"stop:{r['id']}")]]))
            db.execute("UPDATE reminders SET next_at=%s, send_count=send_count+1 WHERE id=%s", (nxt, r["id"]))
        except Exception as e:
            db.log_event("error", f"reminder {r['id']} failed: {e}")
            db.execute("UPDATE reminders SET next_at=%s WHERE id=%s", (db.now() + timedelta(hours=1), r["id"]))


async def daily_job(ctx: ContextTypes.DEFAULT_TYPE):
    today = (db.now() + timedelta(minutes=5)).date()      # اجرا در ۰۰:۰۰ → گزارش روز قبل
    start = datetime.combine(today - timedelta(days=1), dtime.min)
    text = await asyncio.to_thread(services.report_text, "گزارش روزانه", start, datetime.combine(today, dtime.min))
    for uid in services.recipients():
        try:
            await ctx.bot.send_message(uid, text)
        except Exception as e:
            db.log_event("error", f"daily report to {uid} failed: {e}")


async def on_error(update, ctx):
    log.error("handler error", exc_info=ctx.error)
    db.log_event("error", repr(ctx.error))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # جلوگیری از افشای توکن در لاگ URL
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است.")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "menu"], start))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(remind_job, interval=60, first=15)
    app.job_queue.run_daily(daily_job, time=dtime(0, 0, tzinfo=config.TZ))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
