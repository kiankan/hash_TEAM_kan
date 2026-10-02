#!/usr/bin/env python3
from pathlib import Path
import shutil,sys,py_compile,os

APP=Path("/opt/hashbot")
if not APP.exists(): raise SystemExit("❌ /opt/hashbot پیدا نشد")
files=["app/config.py","app/nobitex.py","app/bot.py","app/web.py","app/templates/settings.html"]
backup=APP/"backups"/"upgrade_admin_nobitex"; backup.mkdir(parents=True,exist_ok=True)
for f in files:
    p=APP/f
    if not p.exists(): raise SystemExit(f"❌ فایل پیدا نشد: {p}")
    shutil.copy2(p,backup/f.replace("/","__"))

def save(f,s): (APP/f).write_text(s,encoding="utf-8")

# config
p=APP/"app/config.py"; s=p.read_text(encoding="utf-8")
if "ADMIN_PANEL_URL" not in s:
    s=s.replace('BACKUP_DIR = Path(os.getenv("BACKUP_DIR", BASE_DIR / "backups"))',
                'BACKUP_DIR = Path(os.getenv("BACKUP_DIR", BASE_DIR / "backups"))\nDOMAIN = os.getenv("DOMAIN", "").strip()\nADMIN_PANEL_URL = f"https://{DOMAIN}/admin" if DOMAIN else "/admin"')
save("app/config.py",s)

# nobitex
p=APP/"app/nobitex.py"; s=p.read_text(encoding="utf-8")
if "CRED_FILE" not in s:
    s=s.replace("import time\nfrom decimal import Decimal","import json\nimport os\nimport time\nfrom decimal import Decimal",1)
    s=s.replace('URL = "https://api.nobitex.ir/market/stats"', '''URL = "https://api.nobitex.ir/market/stats"
CRED_FILE = config.BACKUP_DIR / ".nobitex_credentials.json"
def get_credentials():
    try:
        with CRED_FILE.open("r",encoding="utf-8") as f:
            d=json.load(f)
            return str(d.get("api_key","")).strip(),str(d.get("api_secret","")).strip()
    except Exception:
        return config.NOBITEX_API_KEY,os.getenv("NOBITEX_API_SECRET","").strip()
def save_credentials(api_key,api_secret):
    config.BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    tmp=CRED_FILE.with_suffix(".tmp")
    with tmp.open("w",encoding="utf-8") as f:
        json.dump({"api_key":api_key.strip(),"api_secret":api_secret.strip()},f)
    os.chmod(tmp,0o600); os.replace(tmp,CRED_FILE)
def credentials_status():
    k,s=get_credentials(); return bool(k),bool(s)''',1)
    s=s.replace('''_session = requests.Session()
if config.NOBITEX_API_KEY:
    _session.headers.update({"Authorization": f"Token {config.NOBITEX_API_KEY}"})
''',"")
    s=s.replace('''r = _session.post(URL, data={"srcCurrency": src.lower(), "dstCurrency": dst.lower()}, timeout=10)''',
'''api_key,api_secret=get_credentials()
        headers={}
        if api_key: headers["Authorization"]=f"Token {api_key}"
        if api_secret: headers["X-API-Secret"]=api_secret
        r=_session.post(URL,data={"srcCurrency":src.lower(),"dstCurrency":dst.lower()},headers=headers,timeout=10)''',1)
    s += "\n\ndef ton_price(): return irt_rate('ton')\ndef trx_price(): return irt_rate('trx')\n"
save("app/nobitex.py",s)

# bot
p=APP/"app/bot.py"; s=p.read_text(encoding="utf-8")
s=s.replace('''async def show_menu(update): await reply(update,"🏠 منوی اصلی",MENU)''','''async def show_menu(update):
    markup=MENU
    if update.effective_user and update.effective_user.id in config.ADMIN_IDS:
        rows=[list(r) for r in MENU.inline_keyboard]
        rows.append([InlineKeyboardButton("🛠 پنل ادمین",url=config.ADMIN_PANEL_URL)])
        markup=M(rows)
    await reply(update,"🏠 منوی اصلی",markup)''',1)
s=s.replace('''"trx":"TRX","ترون":"TRX","ترونکلاسیک":"TRX",
}''','''"trx":"TRX","ترون":"TRX","ترونکلاسیک":"TRX",
 "btc":"BTC","بیتکوین":"BTC","eth":"ETH","اتریوم":"ETH","usdt":"USDT","تتر":"USDT",
}''',1)
s=s.replace('''ton|تون|gram|گرام|گرم|trx|ترون|ترونکلاسیک)''','''ton|تون|gram|گرام|گرم|trx|ترون|ترونکلاسیک|btc|بیتکوین|eth|اتریوم|usdt|تتر)''',1)
old='''@guard
async def on_text(update,ctx):
 # اول قیمت‌های کوتاه گروه؛ اگر flow فعال باشد، جریان اصلی اولویت دارد.
 f=ctx.user_data.get("flow")
 if not f:
  if await price_reply(update,ctx): return
  return await show_menu(update)
 await FLOWS[f["name"]](update,ctx,update.message.text or "")
'''
new='''@guard
async def on_text(update,ctx):
 text=(update.message.text or "").strip()
 f=ctx.user_data.get("flow")
 if not f:
  if await price_reply(update,ctx): return
  expense=re.fullmatch(r"(?:هزینه|expense)\\s+(.+)",text,re.I)
  candidate=expense.group(1).strip() if expense else text
  h=services.normalize_hash(candidate)
  if h:
   detected=await asyncio.to_thread(services.detect_tx,h)
   if detected:
    kind="expense" if expense else "income"
    tid,value=await asyncio.to_thread(services.add_tx,h,detected["network"],detected["currency"],detected["amount"],kind,"ثبت خودکار از روی Hash",update.effective_user.id)
    if tid:
     worth=f"\\n🇮🇷 ارزش: {services.fmt(value)} تومان" if value is not None else ""
     return await reply(update,f"✅ تراکنش خودکار ثبت شد\\n💎 {detected['amount'].normalize()} {detected['currency']} ({detected['network']}){worth}")
    return await reply(update,"⛔ این Hash قبلاً ثبت شده است.")
   ctx.user_data["flow"]={"name":"add","step":"hash","d":{}}
   return await add_step(update,ctx,candidate)
  return await show_menu(update)
 await FLOWS[f["name"]](update,ctx,text)
'''
if old in s: s=s.replace(old,new,1)
if 'B("💱 قیمت ارز", "prices")' not in s:
    s=s.replace('''[B("👤 Fragment", "frag")]])''','''[B("💱 قیمت ارز", "prices"), B("👤 Fragment", "frag")]])''',1)
if 'cmd=="prices"' not in s and ' elif cmd=="rep":' in s:
    s=s.replace(''' elif cmd=="rep":''',''' elif cmd=="prices":
  await reply(update,"💱 قیمت سریع:\\n• ton / تون / gram / گرام\\n• trx / ترون\\n• btc / بیتکوین\\n• eth / اتریوم\\n• usdt / تتر\\n• مقدار هم قابل محاسبه است؛ مثل <code>0.30 ton</code>",CANCEL)
 elif cmd=="rep":''',1)
save("app/bot.py",s)

# web
p=APP/"app/web.py"; s=p.read_text(encoding="utf-8")
old='''    if request.method == "POST":
        row = db.one("SELECT * FROM admins WHERE username=%s", (session["admin"],))'''
new='''    if request.method == "POST":
        if request.form.get("action") == "nobitex":
            key=request.form.get("nobitex_api_key","").strip()
            secret=request.form.get("nobitex_api_secret","").strip()
            if len(key)>512 or len(secret)>512:
                flash("کلید نوبیتکس بیش از حد طولانی است.","err")
            else:
                nobitex.save_credentials(key,secret)
                flash("کلید و Secret نوبیتکس ذخیره شد.","ok")
            return back("settings")
        row = db.one("SELECT * FROM admins WHERE username=%s", (session["admin"],))'''
if 'request.form.get("action") == "nobitex"' not in s: s=s.replace(old,new,1)
s=s.replace('''    return render_template("settings.html", usdt=nobitex.irt_rate("usdt"))''','''    key_ok,secret_ok=nobitex.credentials_status()
    return render_template("settings.html",usdt=nobitex.irt_rate("usdt"),
                           nobitex_key_ok=key_ok,nobitex_secret_ok=secret_ok)''',1)
save("app/web.py",s)

# settings
p=APP/"app/templates/settings.html"; s=p.read_text(encoding="utf-8")
if "nobitex_api_key" not in s:
    s=s.replace('''<p class="mut">قیمت لحظه‌ای USDT از نوبیتکس: {{ usdt|irt }} تومان</p>''','''<h2>اتصال نوبیتکس</h2>
<div class="card">
<form method="post" class="row" style="flex-direction:column;align-items:stretch;max-width:520px">
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
<input type="hidden" name="action" value="nobitex">
<input name="nobitex_api_key" type="password" placeholder="Nobitex API Key" autocomplete="off">
<input name="nobitex_api_secret" type="password" placeholder="Nobitex Secret Key" autocomplete="off">
<button>🔐 ذخیره کلیدهای نوبیتکس</button>
</form>
<p class="mut">API Key: {{ "✅ تنظیم شده" if nobitex_key_ok else "❌ تنظیم نشده" }} · Secret: {{ "✅ تنظیم شده" if nobitex_secret_ok else "❌ تنظیم نشده" }}</p>
</div>
<p class="mut">قیمت لحظه‌ای USDT از نوبیتکس: {{ usdt|irt }} تومان</p>''',1)
save("app/templates/settings.html",s)

for f in ["app/config.py","app/nobitex.py","app/bot.py","app/web.py"]:
    py_compile.compile(str(APP/f),doraise=True)
print("✅ ارتقا اعمال شد")
