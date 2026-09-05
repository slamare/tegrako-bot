#!/usr/bin/env bash
set -uo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${GREEN}==>${RESET} $1"; }
warn()  { echo -e "${YELLOW}!!${RESET} $1"; }
fail()  { echo -e "${RED}xx${RESET} $1"; }

PROJECT_DIR="/opt/tegrakobot"
STABLE_REF="stable"
REPO_URL="https://github.com/slamare/tegrako-bot.git"
RAW_VERSION_URL="https://raw.githubusercontent.com/slamare/tegrako-bot/${STABLE_REF}/VERSION"
ENV_FILE="${PROJECT_DIR}/.env"
BACKUP_DIR="${PROJECT_DIR}/backups"

[ "$(id -u)" -eq 0 ] || { fail "Запусти от root."; exit 1; }
[ -f "$ENV_FILE" ] || { fail "Бот не установлен (${ENV_FILE} не найден). Запусти install.sh."; exit 1; }

cd "$PROJECT_DIR"

# ── .env helpers ───────────────────────────────────────────────────────────

env_get() {
    grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-
}

env_set() {
    local key="$1" val="$2"
    val="${val//&/\\&}"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

mask() {
    local v="$1"
    [ -z "$v" ] && { echo "(пусто)"; return; }
    local len=${#v}
    if [ "$len" -le 4 ]; then
        echo "****"
    else
        echo "****${v: -4}"
    fi
}

pause() { read -rp "Enter — продолжить..." _; }

# ── Статус ─────────────────────────────────────────────────────────────────

cmd_status() {
    echo -e "\n${BOLD}Статус${RESET}"
    docker compose ps
    echo
    local local_ver git_hash
    local_ver=$(cat "${PROJECT_DIR}/VERSION" 2>/dev/null || echo "?")
    git_hash=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "?")
    echo "Версия: ${local_ver} (${git_hash})"
    pause
}

# ── Старт/стоп/рестарт ───────────────────────────────────────────────────────

cmd_power() {
    echo -e "\n${BOLD}1) Старт  2) Стоп  3) Рестарт  0) Назад${RESET}"
    read -rp "> " c
    case "$c" in
        1) docker compose up -d ;;
        2) docker compose stop ;;
        3) docker compose restart ;;
        *) return ;;
    esac
    pause
}

# ── Логи ───────────────────────────────────────────────────────────────────

cmd_logs() {
    echo -e "\n${BOLD}1) Бот  2) БД  3) Бэкап  4) Всё  0) Назад${RESET}"
    read -rp "> " c
    case "$c" in
        1) docker compose logs -f --tail 100 tegrakobot ;;
        2) docker compose logs -f --tail 100 db ;;
        3) docker compose logs -f --tail 100 backup ;;
        4) docker compose logs -f --tail 100 ;;
        *) return ;;
    esac
}

# ── Обновление ─────────────────────────────────────────────────────────────

apply_migrations() {
    local pgpass; pgpass=$(env_get POSTGRES_PASSWORD)
    docker exec -e PGPASSWORD="$pgpass" -T tegrakobot-db \
        psql -U tegrakobot -d tegrakobot -c \
        "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT now());" >/dev/null

    local f name applied
    for f in "${PROJECT_DIR}"/migrations/*.sql; do
        [ -e "$f" ] || continue
        name=$(basename "$f")
        applied=$(docker exec -e PGPASSWORD="$pgpass" -T tegrakobot-db \
            psql -U tegrakobot -d tegrakobot -tAc \
            "SELECT 1 FROM schema_migrations WHERE filename='${name}'")
        if [ "$applied" != "1" ]; then
            info "Применяю миграцию ${name}..."
            docker exec -e PGPASSWORD="$pgpass" -i tegrakobot-db \
                psql -U tegrakobot -d tegrakobot < "$f"
            docker exec -e PGPASSWORD="$pgpass" -T tegrakobot-db \
                psql -U tegrakobot -d tegrakobot -c \
                "INSERT INTO schema_migrations(filename) VALUES ('${name}');" >/dev/null
        fi
    done
}

cmd_update() {
    echo -e "\n${BOLD}Обновление${RESET}"

    local remote_ver local_ver
    local_ver=$(cat "${PROJECT_DIR}/VERSION" 2>/dev/null || echo 0)
    if remote_ver=$(curl -fsSL --max-time 5 "$RAW_VERSION_URL" 2>/dev/null); then
        remote_ver="${remote_ver//[^0-9]/}"
        if [ -n "$remote_ver" ] && [ "$remote_ver" -le "$local_ver" ]; then
            info "Уже установлена последняя стабильная версия (${local_ver})."
        fi
    else
        warn "Не удалось проверить версию в репозитории — обновляюсь вслепую."
    fi

    git fetch --tags origin >/dev/null 2>&1

    local stashed=0
    if ! git diff --quiet || ! git diff --cached --quiet; then
        git stash push -u -m "tegrako-auto-update" >/dev/null
        stashed=1
    fi

    git checkout -B "$STABLE_REF" "tags/${STABLE_REF}" >/dev/null 2>&1 \
        || { fail "Не удалось переключиться на тег ${STABLE_REF}."; return; }

    if [ "$stashed" = 1 ]; then
        if ! git stash pop; then
            fail "Конфликт при обновлении. Изменения сохранены в git stash."
            warn "Разреши вручную: cd ${PROJECT_DIR} && git status"
            warn "Контейнеры не трогаю, чтобы не поднять бота на битом дереве."
            pause
            return
        fi
    fi

    apply_migrations
    docker compose up -d --build
    install -m 755 "${PROJECT_DIR}/deploy/tegrako.sh" /usr/local/bin/tegrako

    info "Обновлено до версии $(cat "${PROJECT_DIR}/VERSION" 2>/dev/null || echo '?')."
    pause
}

# ── Настройки ──────────────────────────────────────────────────────────────

edit_var() {
    local key="$1" label="$2" secret="${3:-0}"
    local current shown new
    current=$(env_get "$key")
    if [ "$secret" = "1" ]; then
        shown=$(mask "$current")
    else
        shown="${current:-(пусто)}"
    fi
    read -rp "$(echo -e "${BOLD}${label}${RESET} сейчас: ${shown}. Новое значение (Enter — оставить): ")" new
    [ -n "$new" ] && env_set "$key" "$new"
}

cmd_settings() {
    while true; do
        echo -e "\n${BOLD}Настройки${RESET}"
        echo "1) Панель Remnawave"
        echo "2) Платежи"
        echo "3) Брендинг"
        echo "4) Вебхук"
        echo "5) Прокси Telegram"
        echo "6) Бэкапы"
        echo "7) Прочее (админы, интервал проверки нод)"
        echo "0) Назад"
        read -rp "> " c
        case "$c" in
            1)
                edit_var PANEL_API_URL "URL панели"
                edit_var PANEL_API_KEY "API-ключ панели" 1
                edit_var DEFAULT_SQUAD_UUID "UUID дефолтного сквада"
                edit_var ADMIN_GRANT_SQUAD_UUID "UUID сквада для безоплатной выдачи"
                ;;
            2)
                edit_var PAYMENT_REQUISITES "Реквизиты оплаты"
                edit_var DEVICE_SLOT_PRICE "Цена доп. слота устройства"
                edit_var REFERRAL_DISCOUNT_PERCENT "Реферальная скидка, %"
                ;;
            3)
                edit_var BOT_NAME "Название бота"
                edit_var WELCOME_IMAGE_URL "URL картинки /start"
                edit_var SUPPORT_LINK "Ссылка на поддержку"
                edit_var NOTIFY_EXPIRY_DAYS "Дни напоминаний об истечении"
                ;;
            4)
                edit_var WEBHOOK_SECRET "Секрет вебхука" 1
                edit_var WEBHOOK_PORT "Порт вебхука"
                ;;
            5)
                edit_var TELEGRAM_BOT_PROXY "Прокси для Telegram API"
                ;;
            6)
                edit_var BACKUP_INTERVAL_SECONDS "Интервал автобэкапа, сек"
                edit_var BACKUP_RETENTION_DAYS "Хранить бэкапы, дней"
                ;;
            7)
                edit_var ADMIN_IDS "Telegram ID админов"
                edit_var NODE_CHECK_INTERVAL_MINUTES "Интервал проверки нод, мин"
                ;;
            0) return ;;
            *) continue ;;
        esac
        if ask_restart; then
            docker compose up -d --force-recreate
            info "Перезапущено."
        fi
    done
}

ask_restart() {
    local v
    read -rp "$(echo -e "${BOLD}Перезапустить бота сейчас, чтобы применить изменения?${RESET} [y/N]: ")" v
    [[ "$v" =~ ^[Yy] ]]
}

# ── Бэкап / восстановление ───────────────────────────────────────────────

cmd_backup_now() {
    mkdir -p "$BACKUP_DIR"
    local pgpass ts file
    pgpass=$(env_get POSTGRES_PASSWORD)
    ts=$(date +%Y%m%d_%H%M%S)
    file="${BACKUP_DIR}/tegrakobot_${ts}.sql.gz"
    docker exec -e PGPASSWORD="$pgpass" tegrakobot-db pg_dump -U tegrakobot tegrakobot | gzip > "${file}.tmp" \
        && mv "${file}.tmp" "$file" \
        && info "Бэкап создан: ${file}" \
        || fail "Бэкап не удался."
    pause
}

cmd_restore() {
    mkdir -p "$BACKUP_DIR"
    mapfile -t files < <(ls -1 "$BACKUP_DIR"/*.sql.gz 2>/dev/null | sort -r)
    if [ ${#files[@]} -eq 0 ]; then
        warn "Бэкапов не найдено."
        pause
        return
    fi
    echo -e "\n${BOLD}Доступные бэкапы:${RESET}"
    local i=1
    for f in "${files[@]}"; do
        echo "$i) $(basename "$f")"
        i=$((i+1))
    done
    echo "0) Назад"
    read -rp "> " c
    [ "$c" = "0" ] && return
    [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -le "${#files[@]}" ] || { fail "Неверный номер."; return; }
    local chosen="${files[$((c-1))]}"

    warn "Это ПЕРЕЗАПИШЕТ текущую БД содержимым: $(basename "$chosen")"
    read -rp "Введи 'yes' для подтверждения: " confirm
    [ "$confirm" = "yes" ] || { info "Отменено."; return; }

    local pgpass; pgpass=$(env_get POSTGRES_PASSWORD)
    mkdir -p "$BACKUP_DIR"
    local snapshot="${BACKUP_DIR}/tegrakobot_prerestore_$(date +%Y%m%d_%H%M%S).sql.gz"
    info "Снимаю снимок текущего состояния перед восстановлением: ${snapshot}"
    docker exec -e PGPASSWORD="$pgpass" tegrakobot-db pg_dump -U tegrakobot tegrakobot | gzip > "$snapshot"

    docker compose stop tegrakobot
    zcat "$chosen" | docker exec -e PGPASSWORD="$pgpass" -i tegrakobot-db psql -U tegrakobot tegrakobot
    docker compose start tegrakobot
    info "Восстановлено из $(basename "$chosen")."
    pause
}

# ── Удаление ───────────────────────────────────────────────────────────────

confirm_word() {
    local word="$1" prompt="$2"
    read -rp "$(echo -e "${YELLOW}${prompt}${RESET} Введи «${word}» для подтверждения: ")" v
    [ "$v" = "$word" ]
}

cmd_delete() {
    while true; do
        echo -e "\n${BOLD}Удаление${RESET}"
        echo "1) Удалить логи (пересоздать контейнеры)"
        echo "2) Удалить бэкапы"
        echo "3) Откатить .env на дефолт (новые секреты)"
        echo "4) Удалить БД (снести данные, с авто-бэкапом)"
        echo "5) Удалить бота полностью (контейнеры + БД + команда tegrako)"
        echo "0) Назад"
        read -rp "> " c
        local bot_name; bot_name=$(env_get BOT_NAME)
        [ -z "$bot_name" ] && bot_name="TegrakoVPN"
        case "$c" in
            1)
                confirm_word "yes" "Логи текущих контейнеров будут потеряны." || { info "Отменено."; continue; }
                docker compose down && docker compose up -d
                info "Готово."
                ;;
            2)
                confirm_word "yes" "Все файлы в ${BACKUP_DIR} будут удалены." || { info "Отменено."; continue; }
                rm -f "${BACKUP_DIR}"/*.sql.gz
                info "Бэкапы удалены."
                ;;
            3)
                confirm_word "yes" "Текущий .env будет заменён дефолтным (бот перестанет работать до повторной настройки)." || { info "Отменено."; continue; }
                cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
                cp "${PROJECT_DIR}/.env.example" "$ENV_FILE"
                env_set POSTGRES_PASSWORD "$(openssl rand -hex 24)"
                env_set WEBHOOK_SECRET "$(openssl rand -hex 32)"
                warn "Старый .env сохранён рядом как .env.bak.*. Настрой заново: tegrako → пункт 5."
                ;;
            4)
                confirm_word "$bot_name" "БД будет полностью удалена и пересоздана пустой." || { info "Отменено."; continue; }
                cmd_backup_now
                docker compose down
                docker volume rm tegrakobot-db-data 2>/dev/null || true
                docker compose up -d --build
                info "БД пересоздана пустой."
                ;;
            5)
                confirm_word "$bot_name" "Бот, БД и все контейнеры будут удалены безвозвратно. Репозиторий на диске останется." || { info "Отменено."; continue; }
                mkdir -p /tmp/tegrako-final-backup
                local pgpass; pgpass=$(env_get POSTGRES_PASSWORD)
                docker exec -e PGPASSWORD="$pgpass" tegrakobot-db pg_dump -U tegrakobot tegrakobot 2>/dev/null | gzip > "/tmp/tegrako-final-backup/tegrakobot_$(date +%Y%m%d_%H%M%S).sql.gz" || true
                docker compose down -v
                rm -f /usr/local/bin/tegrako
                info "Удалено. Финальный бэкап: /tmp/tegrako-final-backup/"
                echo "Код бота остался в ${PROJECT_DIR} — можно переустановить через install.sh."
                exit 0
                ;;
            0) return ;;
            *) continue ;;
        esac
        pause
    done
}

# ── Диагностика ───────────────────────────────────────────────────────────

cmd_diag() {
    echo -e "\n${BOLD}Диагностика${RESET}"

    echo -n "DNS внутри контейнера бота: "
    if docker exec tegrakobot getent hosts github.com >/dev/null 2>&1; then
        echo "OK"
    else
        echo "ОШИБКА — DNS не резолвится внутри контейнера"
    fi

    local panel_url; panel_url=$(env_get PANEL_API_URL)
    echo -n "Доступность панели (${panel_url}): "
    if curl -sfI --max-time 5 "$panel_url" >/dev/null 2>&1; then
        echo "OK"
    else
        echo "ОШИБКА или недоступна"
    fi

    local telemt_url; telemt_url=$(env_get TELEMT_API_URL)
    if [ -n "$telemt_url" ]; then
        echo -n "Доступность telemt (${telemt_url}): "
        curl -sfI --max-time 5 "$telemt_url" >/dev/null 2>&1 && echo "OK" || echo "ОШИБКА или недоступен"
    fi

    echo -n "Здоровье БД: "
    docker inspect --format='{{.State.Health.Status}}' tegrakobot-db 2>/dev/null || echo "нет healthcheck"

    echo -e "\nПамять:"; free -h | head -2
    echo -e "\nДиск:"; df -h "$PROJECT_DIR" | tail -1

    echo -e "\nЛокальные изменения в репозитории:"
    git -C "$PROJECT_DIR" status --short || echo "чисто"

    pause
}

# ── Меню ───────────────────────────────────────────────────────────────────

while true; do
    echo -e "\n${BOLD}Tegrako Bot — управление${RESET}"
    echo "1) Статус"
    echo "2) Старт / Стоп / Рестарт"
    echo "3) Логи"
    echo "4) Обновление"
    echo "5) Настройки"
    echo "6) Бэкап вручную"
    echo "7) Восстановление из бэкапа"
    echo "8) Удаление"
    echo "9) Диагностика"
    echo "0) Выход"
    read -rp "> " choice
    case "$choice" in
        1) cmd_status ;;
        2) cmd_power ;;
        3) cmd_logs ;;
        4) cmd_update ;;
        5) cmd_settings ;;
        6) cmd_backup_now ;;
        7) cmd_restore ;;
        8) cmd_delete ;;
        9) cmd_diag ;;
        0) exit 0 ;;
        *) ;;
    esac
done
