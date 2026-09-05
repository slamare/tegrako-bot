#!/usr/bin/env bash
set -euo pipefail

INSTALL_SH_VERSION=1

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${GREEN}==>${RESET} $1"; }
warn()  { echo -e "${YELLOW}!!${RESET} $1"; }
fail()  { echo -e "${RED}xx${RESET} $1"; exit 1; }

REPO_URL="https://github.com/slamare/tegrako-bot.git"
PROJECT_DIR="/opt/tegrakobot"
STABLE_REF="stable"
RAW_VERSION_URL="https://raw.githubusercontent.com/slamare/tegrako-bot/${STABLE_REF}/VERSION"
ENV_FILE="${PROJECT_DIR}/.env"

# ── Root и ОС ────────────────────────────────────────────────────────────

[ "$(id -u)" -eq 0 ] || fail "Запусти скрипт от root (sudo -i, затем повтори команду)."

if [ -r /etc/os-release ]; then
    . /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
        *debian*|*ubuntu*) ;;
        *) fail "Поддерживаются только Debian/Ubuntu. Обнаружено: ${PRETTY_NAME:-неизвестно}." ;;
    esac
else
    fail "Не удалось определить ОС (/etc/os-release отсутствует)."
fi

# ── Уже установлено? ─────────────────────────────────────────────────────

if [ -f "$ENV_FILE" ]; then
    info "Tegrako Bot уже установлен в ${PROJECT_DIR}."
    if [ -x /usr/local/bin/tegrako ]; then
        exec /usr/local/bin/tegrako
    elif [ -f "${PROJECT_DIR}/deploy/tegrako.sh" ]; then
        exec bash "${PROJECT_DIR}/deploy/tegrako.sh"
    else
        fail "Команда tegrako не найдена. Переустанови: rm -rf ${PROJECT_DIR} и запусти скрипт заново."
    fi
fi

# ── Самопроверка версии установщика ──────────────────────────────────────

if remote_version=$(curl -fsSL --max-time 5 "$RAW_VERSION_URL" 2>/dev/null); then
    remote_version="${remote_version//[^0-9]/}"
    if [ -n "$remote_version" ] && [ "$remote_version" -gt "$INSTALL_SH_VERSION" ]; then
        fail "Доступна новая версия установщика. Перезапусти:\ncurl -fsSL https://raw.githubusercontent.com/slamare/tegrako-bot/${STABLE_REF}/install.sh | bash"
    fi
else
    warn "Не удалось проверить актуальность установщика — продолжаю с текущей версией."
fi

# ── Зависимости (Debian/Ubuntu) ───────────────────────────────────────────

install_pkg() {
    command -v "$1" >/dev/null 2>&1 && return 0
    info "Устанавливаю $1..."
    apt-get update -qq
    apt-get install -y -qq "$2"
}

install_pkg git git
install_pkg curl curl
install_pkg openssl openssl

if ! command -v docker >/dev/null 2>&1; then
    info "Устанавливаю Docker..."
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin не найден даже после установки Docker. Установи вручную: https://docs.docker.com/compose/install/"

random_hex() {
    local bytes="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        python3 -c "import secrets; print(secrets.token_hex($bytes))"
    fi
}

# ── Помощники ввода ───────────────────────────────────────────────────────

ask_required() {
    local prompt="$1"
    local value=""
    while [ -z "$value" ]; do
        read -rp "$(echo -e "${BOLD}${prompt}${RESET}: ")" value
        [ -z "$value" ] && warn "Значение обязательно, попробуй ещё раз."
    done
    echo "$value"
}

ask_default() {
    local prompt="$1"
    local default="$2"
    local value=""
    read -rp "$(echo -e "${BOLD}${prompt}${RESET} [${default}]: ")" value
    echo "${value:-$default}"
}

ask_yn() {
    local prompt="$1"
    local default="${2:-n}"
    local hint="y/N"
    [ "$default" = "y" ] && hint="Y/n"
    local value=""
    read -rp "$(echo -e "${BOLD}${prompt}${RESET} [${hint}]: ")" value
    value="${value:-$default}"
    [[ "$value" =~ ^[Yy] ]]
}

echo -e "${BOLD}Tegrako Bot — установка${RESET}\n"

# ── Клонирование ──────────────────────────────────────────────────────────

info "Клонирую репозиторий в ${PROJECT_DIR}..."
git clone --branch "$STABLE_REF" --depth 1 "$REPO_URL" "$PROJECT_DIR"
cd "$PROJECT_DIR"

# ── Обязательное ──────────────────────────────────────────────────────────

echo -e "\n${BOLD}1/6 — Telegram${RESET}"
BOT_TOKEN=$(ask_required "Токен бота от @BotFather")
ADMIN_IDS=$(ask_required "Твой Telegram ID (число, через запятую если несколько админов)")

echo -e "\n${BOLD}2/6 — Remnawave Panel (панель уже должна быть установлена отдельно)${RESET}"
PANEL_API_URL=$(ask_required "URL панели (например https://panel.example.com)")
PANEL_API_KEY=$(ask_required "API-ключ панели")
DEFAULT_SQUAD_UUID=$(ask_required "UUID дефолтного сквада (Remnawave → Internal Squads)")

echo -e "\n${BOLD}3/6 — База данных${RESET}"
if ask_yn "Сгенерировать пароль Postgres автоматически?" y; then
    POSTGRES_PASSWORD=$(random_hex 24)
    info "Пароль сгенерирован."
else
    while true; do
        POSTGRES_PASSWORD=$(ask_required "Пароль для Postgres (только буквы, цифры, - _ .)")
        [[ "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9._-]+$ ]] && break
        warn "Символы вроде @ : / ? # ломают строку подключения к базе. Используй только буквы, цифры, - _ ."
    done
fi
DATABASE_URL="postgresql+asyncpg://tegrakobot:${POSTGRES_PASSWORD}@db:5432/tegrakobot"

# ── Платежи ────────────────────────────────────────────────────────────────

echo -e "\n${BOLD}4/6 — Оплата${RESET}"
PAYMENT_REQUISITES=$(ask_default "Реквизиты оплаты (\"Название|Реквизиты\" через ;, можно заполнить позже)" "")
DEVICE_SLOT_PRICE=$(ask_default "Цена доп. слота устройства, ₽ (0 = отключено)" "0")
REFERRAL_DISCOUNT_PERCENT=$(ask_default "Скидка для приглашённых на первую покупку, %" "5")

# ── Прокси и вебхук ────────────────────────────────────────────────────────

echo -e "\n${BOLD}5/6 — Прокси и вебхук${RESET}"

TELEGRAM_BOT_PROXY=""
if ask_yn "Есть готовый SOCKS5/HTTP-прокси для доступа к Telegram API?" n; then
    TELEGRAM_BOT_PROXY=$(ask_required "Адрес прокси (например socks5://127.0.0.1:1080)")
fi

WEBHOOK_SECRET=""
WEBHOOK_PORT="9090"
if ask_yn "Настроить приём вебхуков от Remnawave-панели?" y; then
    if ask_yn "Сгенерировать секрет вебхука автоматически?" y; then
        WEBHOOK_SECRET=$(random_hex 32)
        info "Секрет вебхука сгенерирован."
    else
        WEBHOOK_SECRET=$(ask_required "Секрет вебхука")
    fi
    WEBHOOK_PORT=$(ask_default "Порт для вебхуков панели" "9090")
fi

TELEMT_API_URL=""
TELEMT_PUBLIC_HOST=""
TELEMT_PUBLIC_PORT="8443"
if ask_yn "Настроить MTProto-прокси (telemt) сейчас?" n; then
    TELEMT_API_URL=$(ask_required "URL telemt API (например http://host.docker.internal:9091)")
    TELEMT_PUBLIC_HOST=$(ask_required "Публичный хост telemt (для ссылок пользователям)")
    TELEMT_PUBLIC_PORT=$(ask_default "Публичный порт telemt" "8443")
fi

# ── Брендинг ───────────────────────────────────────────────────────────────

echo -e "\n${BOLD}6/6 — Брендинг${RESET}"
BOT_NAME=$(ask_default "Название бота (видно пользователям)" "TegrakoVPN")
WELCOME_IMAGE_URL=$(ask_default "URL картинки для /start (можно пусто)" "")
SUPPORT_LINK=$(ask_default "Ссылка на поддержку, если отдельная от тикетов в боте (можно пусто)" "")
NOTIFY_EXPIRY_DAYS=$(ask_default "За сколько дней до истечения слать напоминание (через запятую)" "3,1")

# ── Запись .env ────────────────────────────────────────────────────────────

cat > "$ENV_FILE" <<EOF
# Сгенерировано install.sh $(date -Iseconds)

# Telegram
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
TELEGRAM_BOT_PROXY=${TELEGRAM_BOT_PROXY}

# Database
DATABASE_URL=${DATABASE_URL}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# Remnawave Panel
PANEL_API_URL=${PANEL_API_URL}
PANEL_API_KEY=${PANEL_API_KEY}
DEFAULT_SQUAD_UUID=${DEFAULT_SQUAD_UUID}
ADMIN_GRANT_SQUAD_UUID=

# Bot UI
BOT_NAME=${BOT_NAME}
WELCOME_IMAGE_URL=${WELCOME_IMAGE_URL}
SUPPORT_LINK=${SUPPORT_LINK}

# Payment
PAYMENT_REQUISITES=${PAYMENT_REQUISITES}
DEVICE_SLOT_PRICE=${DEVICE_SLOT_PRICE}
REFERRAL_DISCOUNT_PERCENT=${REFERRAL_DISCOUNT_PERCENT}

# Notifications
NOTIFY_EXPIRY_DAYS=${NOTIFY_EXPIRY_DAYS}

# Webhook (Remnawave -> бот)
WEBHOOK_SECRET=${WEBHOOK_SECRET}
WEBHOOK_PORT=${WEBHOOK_PORT}

# Внешняя TCP-проверка доступности нод
NODE_CHECK_INTERVAL_MINUTES=5

# MTProto прокси (telemt), опционально
TELEMT_API_URL=${TELEMT_API_URL}
TELEMT_PUBLIC_HOST=${TELEMT_PUBLIC_HOST}
TELEMT_PUBLIC_PORT=${TELEMT_PUBLIC_PORT}

# Резервные копии БД
BACKUP_INTERVAL_SECONDS=21600
BACKUP_RETENTION_DAYS=14
EOF

info ".env записан."

# ── Docker network ───────────────────────────────────────────────────────

if ! docker network inspect remnawave-network >/dev/null 2>&1; then
    warn "Внешняя сеть 'remnawave-network' не найдена."
    if ask_yn "Создать её сейчас?" y; then
        docker network create remnawave-network
        info "Сеть создана."
    else
        fail "Без сети 'remnawave-network' бот не сможет достучаться до панели. Создай вручную: docker network create remnawave-network"
    fi
fi

# ── Запуск ────────────────────────────────────────────────────────────────

info "Собираю и запускаю контейнеры..."
docker compose up -d --build

# ── Команда tegrako ───────────────────────────────────────────────────────

install -m 755 "${PROJECT_DIR}/deploy/tegrako.sh" /usr/local/bin/tegrako
info "Команда управления установлена: tegrako"

echo
info "Готово! Бот запущен."
[ -n "$WEBHOOK_SECRET" ] && info "Секрет вебхука для панели Remnawave: ${WEBHOOK_SECRET}"
[ -z "$TELEGRAM_BOT_PROXY" ] && warn "Если хостишь бота в стране с ограничениями Telegram — настрой TELEGRAM_BOT_PROXY через 'tegrako' (пункт 5), потребуется отдельно поднятый SOCKS5/HTTP-прокси."
info "Дальнейшее управление: команда 'tegrako' из любого места."
