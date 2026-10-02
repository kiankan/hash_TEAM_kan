#!/usr/bin/env bash
# نصب خودکار Hashbot روی Ubuntu/Debian:  sudo ./install.sh
set -Eeuo pipefail
trap 'echo "❌ خطا در خط $LINENO" >&2' ERR

[[ $EUID -eq 0 ]] || { echo "با sudo اجرا کنید."; exit 1; }
grep -qiE 'ubuntu|debian' /etc/os-release || { echo "فقط Ubuntu/Debian پشتیبانی می‌شود."; exit 1; }

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/hashbot
APP_USER=hashbot
ENV_FILE="$APP_DIR/.env"

ask() { # ask VAR "متن" [secret]
  local var=$1 prompt=$2 secret=${3:-} val
  [[ -n "${!var:-}" ]] && return
  if [[ -n $secret ]]; then read -rsp "$prompt: " val; echo; else read -rp "$prompt: " val; fi
  printf -v "$var" '%s' "$val"
}

echo "== نصب Hashbot =="
# اگر قبلاً نصب شده، مقادیر قبلی را بخوان
if [[ -f $ENV_FILE ]]; then set -a; . "$ENV_FILE"; set +a; echo "(.env موجود خوانده شد)"; fi

ask BOT_TOKEN "توکن ربات تلگرام" secret
ask ADMIN_TELEGRAM_IDS "Telegram ID ادمین(ها) (با کاما جدا کنید)"
ask DOMAIN "دامنهٔ پنل (مثلاً bot.example.com — باید به IP این سرور اشاره کند)"
ask LE_EMAIL "ایمیل برای گواهی SSL"
ask PANEL_USER "نام کاربری ادمین پنل"
if [[ -z "${PANEL_PASS:-}" ]]; then
  while :; do
    read -rsp "رمز ادمین پنل (حداقل ۱۲ کاراکتر): " PANEL_PASS; echo
    read -rsp "تکرار رمز: " p2; echo
    [[ ${#PANEL_PASS} -ge 12 && $PANEL_PASS == "$p2" ]] && break
    echo "رمز کوتاه است یا یکسان نیست."
  done
fi
[[ $BOT_TOKEN =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]           || { echo "فرمت توکن نامعتبر است."; exit 1; }
[[ $ADMIN_TELEGRAM_IDS =~ ^[0-9]+(,[0-9]+)*$ ]]       || { echo "Telegram ID نامعتبر است."; exit 1; }
[[ $DOMAIN =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]       || { echo "دامنه نامعتبر است."; exit 1; }
[[ $PANEL_USER =~ ^[A-Za-z0-9_.-]{3,32}$ ]]           || { echo "نام کاربری نامعتبر است."; exit 1; }

if [[ -e /etc/mysql/FROZEN ]]; then
  echo "❌ MySQL در حالت frozen است (دیتای قدیمی MariaDB روی سرور). README بخش «عیب‌یابی» را ببینید."; exit 1
fi
WEB_PORT=${WEB_PORT:-8000}
if ss -tln | grep -q ":$WEB_PORT " && ! systemctl is-active -q hashbot-web; then
  echo "❌ پورت $WEB_PORT اشغال است. با WEB_PORT=8123 sudo -E ./install.sh دوباره اجرا کنید."; exit 1
fi

echo "== نصب بسته‌ها =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip mariadb-server nginx certbot python3-certbot-nginx \
  ufw git rsync openssl cron curl
systemctl enable --now mariadb cron nginx

echo "== کاربر و فایل‌ها =="
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/backups"
[[ "$SRC_DIR" == "$APP_DIR" ]] || rsync -a --exclude .env --exclude venv --exclude backups "$SRC_DIR/" "$APP_DIR/"
chmod +x "$APP_DIR/manage.sh" "$APP_DIR/install.sh"
ln -sf "$APP_DIR/manage.sh" /usr/local/bin/hashbot

DB_NAME=${DB_NAME:-hashbot}; DB_USER=${DB_USER:-hashbot}
DB_PASSWORD=${DB_PASSWORD:-$(openssl rand -hex 24)}
SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32)}

echo "== دیتابیس (کاربر اختصاصی با حداقل دسترسی) =="
cat > /etc/mysql/conf.d/hashbot.cnf <<CNF
[mysqld]
bind-address = 127.0.0.1
local_infile = 0
CNF
systemctl restart mariadb
mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$DB_USER'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, LOCK TABLES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
mysql "$DB_NAME" < "$APP_DIR/schema.sql"

echo "== .env =="
umask 077
cat > "$ENV_FILE" <<ENV
BOT_TOKEN=$BOT_TOKEN
ADMIN_TELEGRAM_IDS=$ADMIN_TELEGRAM_IDS
DB_HOST=127.0.0.1
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
SECRET_KEY=$SECRET_KEY
TIMEZONE=${TIMEZONE:-Asia/Tehran}
DOMAIN=$DOMAIN
ENV
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"; chmod 700 "$APP_DIR/backups"

echo "== Python =="
runuser -u "$APP_USER" -- python3 -m venv "$APP_DIR/venv"
runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/pip" install -q --upgrade pip
runuser -u "$APP_USER" -- "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
(cd "$APP_DIR" && runuser -u "$APP_USER" -- env ADMIN_USER="$PANEL_USER" ADMIN_PASS="$PANEL_PASS" \
  "$APP_DIR/venv/bin/python" -m app.create_admin)

echo "== systemd =="
for svc in bot web; do
  if [[ $svc == bot ]]; then EXEC="$APP_DIR/venv/bin/python -m app.bot"; DESC="Telegram bot"
  else EXEC="$APP_DIR/venv/bin/gunicorn -w 1 --threads 4 -b 127.0.0.1:$WEB_PORT app.web:app"; DESC="admin panel"; fi
  cat > "/etc/systemd/system/hashbot-$svc.service" <<UNIT
[Unit]
Description=Hashbot $DESC
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$EXEC
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR/backups

[Install]
WantedBy=multi-user.target
UNIT
done
systemctl daemon-reload
systemctl enable --now hashbot-bot hashbot-web

echo "== Nginx + HTTPS =="
cat > /etc/nginx/sites-available/hashbot <<NGINX
limit_req_zone \$binary_remote_addr zone=hashbot:10m rate=10r/s;
server {
    listen 80;
    server_name $DOMAIN;
    server_tokens off;
    client_max_body_size 1m;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location /admin {
        limit_req zone=hashbot burst=20 nodelay;
        proxy_pass http://127.0.0.1:$WEB_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location / { return 404; }
}
NGINX
ln -sf /etc/nginx/sites-available/hashbot /etc/nginx/sites-enabled/hashbot
nginx -t && systemctl reload nginx
if ! certbot --nginx -d "$DOMAIN" -m "$LE_EMAIL" --agree-tos --no-eff-email --redirect -n; then
  echo "⚠️ دریافت SSL ناموفق بود (آیا DNS دامنه به این سرور اشاره می‌کند؟)."
  echo "   بعد از اصلاح: sudo certbot --nginx -d $DOMAIN -m $LE_EMAIL --agree-tos --redirect"
  echo "   تا زمان فعال شدن HTTPS ورود به پنل کار نمی‌کند (کوکی فقط روی HTTPS ارسال می‌شود)."
fi

echo "== فایروال و بکاپ خودکار =="
SSH_PORT=$(awk '/^Port /{print $2; exit}' /etc/ssh/sshd_config 2>/dev/null || true)
if ufw status | grep -q "Status: active"; then
  ufw allow "${SSH_PORT:-22}/tcp" >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
else
  echo "فایروال ufw الان غیرفعال است. فعال‌سازی ممکن است پورت‌های سرویس‌های دیگر این سرور را ببندد."
  read -rp "فعال شود؟ (y/N): " yn
  if [[ $yn == [yY] ]]; then
    ufw allow "${SSH_PORT:-22}/tcp" >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
    ufw --force enable >/dev/null
  fi
fi
echo "0 3 * * * $APP_USER $APP_DIR/manage.sh backup >/dev/null 2>&1" > /etc/cron.d/hashbot
chmod 644 /etc/cron.d/hashbot

cat <<DONE

✅ نصب کامل شد.
   پنل:  https://$DOMAIN/admin   (کاربر: $PANEL_USER)
   ربات: در تلگرام /start بزنید.
   مدیریت: sudo hashbot help
DONE
