#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/hashbot}"
APP_USER="${APP_USER:-hashbot}"
SERVICE="hashbot-bot"

[[ -f "$APP_DIR/.env" ]] && {
    set -a
    . "$APP_DIR/.env"
    set +a
}

need_root() {
    [[ $EUID -eq 0 ]] || {
        echo "❌ این دستور نیاز به sudo/root دارد."
        exit 1
    }
}

status_bot() {
    systemctl --no-pager --full status "$SERVICE" || true
}

start_bot() {
    need_root
    systemctl start "$SERVICE"
    echo "✅ Bot شروع شد."
}

stop_bot() {
    need_root
    systemctl stop "$SERVICE"
    echo "✅ Bot متوقف شد."
}

restart_bot() {
    need_root
    systemctl restart "$SERVICE"
    echo "✅ Bot ری‌استارت شد."
}

logs_bot() {
    journalctl -u "$SERVICE" -n 100 -f
}

update_bot() {
    need_root

    echo "🔄 دریافت آخرین کد..."
    git -C "$APP_DIR" pull --ff-only

    if [[ -x "$APP_DIR/.venv/bin/pip" ]]; then
        "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    elif [[ -x "$APP_DIR/venv/bin/pip" ]]; then
        "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    else
        echo "❌ محیط Python پیدا نشد."
        exit 1
    fi

    if [[ -f "$APP_DIR/schema.sql" ]]; then
        set -a
        . "$APP_DIR/.env"
        set +a

        MYSQL_PWD="$DB_PASSWORD" \
        mysql -h "$DB_HOST" \
              -u "$DB_USER" \
              "$DB_NAME" < "$APP_DIR/schema.sql"
    fi

    systemctl daemon-reload
    systemctl restart "$SERVICE"

    echo "✅ Bot با موفقیت آپدیت و ری‌استارت شد."
}

backup_db() {
    need_root

    mkdir -p "$APP_DIR/backups"

    FILE="$APP_DIR/backups/backup_$(date +%Y%m%d_%H%M%S).sql.gz"

    MYSQL_PWD="$DB_PASSWORD" mysqldump \
        -h "$DB_HOST" \
        -u "$DB_USER" \
        --single-transaction \
        "$DB_NAME" | gzip > "$FILE"

    chmod 600 "$FILE"

    echo "✅ بکاپ ساخته شد:"
    echo "$FILE"
}

list_backups() {
    echo
    echo "📦 بکاپ‌های موجود:"
    echo

    ls -lht "$APP_DIR"/backups/backup_*.sql.gz 2>/dev/null \
        || echo "❌ هیچ بکاپی وجود ندارد."
}

restore_db() {
    need_root

    echo
    read -r -p "مسیر فایل .sql.gz را وارد کنید: " FILE

    [[ -f "$FILE" ]] || {
        echo "❌ فایل پیدا نشد."
        return 1
    }

    echo
    echo "⚠️ هشدار: دیتابیس فعلی با این بکاپ جایگزین می‌شود."
    read -r -p "برای ادامه yes وارد کنید: " CONFIRM

    [[ "$CONFIRM" == "yes" ]] || {
        echo "❌ عملیات لغو شد."
        return 0
    }

    systemctl stop "$SERVICE"

    gunzip -c "$FILE" | MYSQL_PWD="$DB_PASSWORD" mysql \
        -h "$DB_HOST" \
        -u "$DB_USER" \
        "$DB_NAME"

    systemctl start "$SERVICE"

    echo "✅ دیتابیس بازیابی شد."
}

menu() {
    while true; do
        clear

        echo "╔══════════════════════════════════════╗"
        echo "║          HashBot Manager             ║"
        echo "╚══════════════════════════════════════╝"
        echo
        echo "1) وضعیت Bot"
        echo "2) شروع Bot"
        echo "3) توقف Bot"
        echo "4) ری‌استارت Bot"
        echo "5) لاگ زنده Bot"
        echo "6) آپدیت Bot"
        echo "7) ساخت بکاپ دیتابیس"
        echo "8) فهرست بکاپ‌ها"
        echo "9) بازیابی بکاپ"
        echo "0) خروج"
        echo
        read -r -p "انتخاب شما: " CHOICE

        case "$CHOICE" in
            1)
                status_bot
                read -r -p "Enter برای ادامه..."
                ;;
            2)
                start_bot
                read -r -p "Enter برای ادامه..."
                ;;
            3)
                stop_bot
                read -r -p "Enter برای ادامه..."
                ;;
            4)
                restart_bot
                read -r -p "Enter برای ادامه..."
                ;;
            5)
                logs_bot
                ;;
            6)
                update_bot
                read -r -p "Enter برای ادامه..."
                ;;
            7)
                backup_db
                read -r -p "Enter برای ادامه..."
                ;;
            8)
                list_backups
                read -r -p "Enter برای ادامه..."
                ;;
            9)
                restore_db
                read -r -p "Enter برای ادامه..."
                ;;
            0)
                echo "خروج..."
                exit 0
                ;;
            *)
                echo "❌ گزینه نامعتبر است."
                sleep 1
                ;;
        esac
    done
}

COMMAND="${1:-}"

case "$COMMAND" in
    status)
        status_bot
        ;;
    start)
        start_bot
        ;;
    stop)
        stop_bot
        ;;
    restart)
        restart_bot
        ;;
    logs)
        logs_bot
        ;;
    update)
        update_bot
        ;;
    backup)
        backup_db
        ;;
    backups)
        list_backups
        ;;
    restore)
        [[ -n "${2:-}" ]] || {
            echo "استفاده: sudo hashbot restore FILE"
            exit 1
        }
        need_root
        FILE="$2"
        [[ -f "$FILE" ]] || {
            echo "❌ فایل پیدا نشد."
            exit 1
        }

        systemctl stop "$SERVICE"
        gunzip -c "$FILE" | MYSQL_PWD="$DB_PASSWORD" mysql \
            -h "$DB_HOST" \
            -u "$DB_USER" \
            "$DB_NAME"
        systemctl start "$SERVICE"

        echo "✅ دیتابیس بازیابی شد."
        ;;
    "")
        need_root
        menu
        ;;
    *)
        echo "❌ دستور نامعتبر است."
        echo
        echo "برای مشاهده منو:"
        echo "  sudo hashbot"
        exit 1
        ;;
esac
