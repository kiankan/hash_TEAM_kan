# Hashbot — ربات مدیریت تراکنش و یادآوری

ربات تلگرام (Python + MySQL) برای ثبت تراکنش‌ها، محاسبهٔ سود/ضرر، گزارش شبانه، یادآوری پیگیری پرداخت‌ها و بررسی Fragment، به‌همراه پنل ادمین روی دامنه (HTTPS).

## نصب (Ubuntu/Debian)
```bash
git clone https://github.com/kiankan/hash_TEAM_kan.git
cd hash_TEAM_kan
sudo ./install.sh
```
نصب‌کننده توکن ربات، Telegram ID ادمین، دامنه، ایمیل SSL و کاربر/رمز پنل را می‌پرسد و خودش Python، MySQL، Nginx، SSL (Let's Encrypt)، دیتابیس، systemd، فایروال (ufw) و بکاپ شبانه را تنظیم می‌کند. قبل از نصب، رکورد DNS دامنه را به IP سرور وصل کنید.

## قابلیت‌ها
- **ربات:** فقط یک منوی اصلی (ثبت تراکنش، تاریخچه، گزارش امروز، یادآوری‌ها، Fragment).
- **تراکنش:** Hash، شبکه، ارز، مقدار، درآمد/هزینه، توضیحات؛ Hash تکراری رد می‌شود؛ ارزش تومانی با قیمت نوبیتکس در لحظهٔ ثبت ذخیره می‌شود.
- **گزارش روزانه:** هر شب ۰۰:۰۰ (Asia/Tehran) گزارش روز قبل برای کاربران مجاز ارسال می‌شود. اگر قیمت یک ارز هنگام ثبت در دسترس نبوده، موقع گزارش با قیمت همان لحظه تکمیل می‌شود.
- **یادآوری:** پیش‌فرض هر ۷ روز، تا وقتی با دکمهٔ «توقف» (ربات یا پنل) متوقف کنید.
- **Fragment:** لینک یا username → username، وضعیت، قیمت و اطلاعات قابل‌استخراج. تحلیل صفحه best-effort است و اگر Fragment ساختار HTML را عوض کند، `app/fragment.py` نیاز به تنظیم دارد.
- **پنل:** `https://دامنه/admin` — تراکنش‌ها، یادآوری‌ها، گزارش‌ها، کاربران، لاگ‌ها، بکاپ، تغییر رمز.

## مدیریت
```bash
sudo hashbot status | start | stop | restart
sudo hashbot logs [bot|web]
sudo hashbot update            # git pull + وابستگی‌ها + ری‌استارت
sudo hashbot backup | backups
sudo hashbot restore FILE.sql.gz
sudo hashbot passwd            # تغییر رمز ادمین پنل
```

## امنیت
فقط Telegram ID های مجاز (لیست سفید) • رمز پنل با hash (scrypt) • نشست امن (Secure/HttpOnly/SameSite، انقضای ۲ ساعته) • CSRF • Rate Limit روی ورود و Nginx • HTTPS + HSTS • ufw • MySQL فقط روی 127.0.0.1 با کاربر اختصاصی (SELECT/INSERT/UPDATE/DELETE/LOCK) • `.env` با دسترسی 600 • لاگ ورودهای پنل • سرویس‌ها با کاربر غیر root و محدودیت‌های systemd.

## عیب‌یابی
**`MySQL has been frozen` / `incompatible downgrade`:** روی سرور دیتای MariaDB قدیمی وجود دارد و MySQL 8 آن را نمی‌پذیرد. این پروژه از MariaDB استفاده می‌کند (با دیتای MariaDB 10.6 سازگار است):
```bash
systemctl stop mysql 2>/dev/null; tar czf /root/mysql-datadir-backup.tgz -C /var/lib mysql
apt-get remove -y mysql-server mysql-server-8.0 mysql-server-core-8.0 mysql-client-8.0 mysql-client-core-8.0
rm -f /etc/mysql/FROZEN
apt-get install -y mariadb-server
systemctl status mariadb --no-pager | head -5
```
**پورت ۸۰۰۰ اشغال است:** `WEB_PORT=8123 sudo -E ./install.sh`
