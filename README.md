<img width="1920" height="919" alt="tegrako-bot" src="https://github.com/user-attachments/assets/443415fc-82b1-4011-9634-72071de3f9d1" />

# Tegrako Bot

Telegram-бот для TegrakoVPN на базе Remnawave. Управляет подписками, платежами, поддержкой и MTProto-прокси прямо из Telegram.

---

## Возможности

**Пользователь**
- Регистрация, личный кабинет, история платежей
- Покупка и продление подписки, выбор тарифа
- Управление устройствами (HWID)
- MTProto-прокси с автопровизионингом
- Реферальная программа с бонусными днями
- Поддержка через тикеты

**Администратор**
- Управление тарифами (цена, трафик, устройства, сквад, триал)
- Подтверждение/отклонение платежей по скриншоту
- Ответы на тикеты, ручное закрытие
- Управление нодами Remnawave
- Рассылка по сегментам (все / активные / истёкшие)
- Статистика выручки и пользователей
- Режим технических работ

**Сервисное**
- Вебхуки от Remnawave → мгновенные уведомления об истечении и лимитах
- Bulk-запрос к панели в scheduler (один запрос вместо N по uuid)
- Scheduler раз в 6 часов как fallback
- Автоотзыв MTProto при просрочке > 5 дней

---

## Стек

| | |
|---|---|
| Python | 3.12 |
| Telegram | aiogram 3.26 |
| Web | aiohttp (webhook-сервер) |
| БД | PostgreSQL + SQLAlchemy async |
| Панель | Remnawave (httpx, прямые запросы) |
| Деплой | Docker Compose |

---

## Структура

```
.
├── bot/
│   ├── handlers/
│   │   ├── user/        # start, payment, support, mtproto
│   │   ├── admin/       # admin
│   │   └── webhook.py   # события от Remnawave
│   ├── middlewares/
│   ├── services/
│   │   ├── remnawave.py
│   │   ├── scheduler.py
│   │   └── telemt.py
│   ├── keyboards/
│   └── states/
├── config/settings.py
├── db/
│   ├── models.py
│   └── dal.py
├── deploy/
│   ├── backup.sh        # автобэкап (контейнер tegrakobot-backup)
│   └── tegrako.sh        # команда управления, ставится в /usr/local/bin/tegrako
├── migrations/            # SQL-миграции поверх текущей схемы, накатывает tegrako
├── main.py
├── install.sh
├── release.sh              # разработчику: релиз стабильной версии
├── VERSION
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

---

## Установка

Одна команда (Ubuntu/Debian, от root):

```bash
curl -fsSL https://raw.githubusercontent.com/slamare/tegrako-bot/stable/install.sh | bash
```

Скрипт сам:
- ставит Docker/git/curl, если их нет
- клонирует стабильную ветку в `/opt/tegrakobot`
- спрашивает токен бота, ID админа, данные уже существующей панели Remnawave (бот её не разворачивает, только подключается по API), пароль БД (авто/ручной), реквизиты оплаты, прокси и вебхук по желанию, брендинг
- поднимает контейнеры и создаёт docker-сеть `remnawave-network`, если её нет
- ставит команду `tegrako` в `/usr/local/bin` — дальше всё управление через неё

Если бот уже установлен — та же команда просто откроет меню `tegrako`, ничего не переустанавливая.

### Ручная установка (если нужен полный контроль)

```bash
git clone --branch stable https://github.com/slamare/tegrako-bot /opt/tegrakobot
cd /opt/tegrakobot
cp .env.example .env
nano .env
docker network ls | grep remnawave-network || docker network create remnawave-network
docker compose up -d --build
install -m 755 deploy/tegrako.sh /usr/local/bin/tegrako
```

`POSTGRES_PASSWORD` должен состоять только из букв/цифр/`-_.` — он подставляется прямо в `DATABASE_URL` без URL-кодирования, символы вроде `@ : / ?` сломают строку подключения.

---

## Управление — команда `tegrako`

После установки всё техническое управление ботом — через `tegrako` (не через ручные docker-команды и не через редактирование `.env` руками):

```
1) Статус                     6) Бэкап вручную
2) Старт / Стоп / Рестарт     7) Восстановление из бэкапа
3) Логи                       8) Удаление (5 уровней, с подтверждением)
4) Обновление                 9) Диагностика
5) Настройки                  0) Выход
```

- **Обновление** тянет стабильный тег, применяет ещё не накатанные SQL-миграции из `migrations/` (отслеживаются в таблице `schema_migrations`), пересобирает контейнеры и обновляет саму команду `tegrako`.
- **Настройки** — визард по `.env` (панель / платежи / брендинг / вебхук / прокси / бэкапы), с показом текущего значения (секреты маскируются) и предложением перезапустить бота после изменения.
- **Удаление** — пять уровней от безобидного (очистить логи) до полного сноса, каждый требует явного текстового подтверждения (`yes` или название бота), самые тяжёлые уровни (снос БД / полное удаление) сначала делают автоматический бэкап.

Бизнес-логика (тарифы, промокоды, пользователи, рассылки) — всё в Telegram через `/admin`, дублировать это в shell-меню не нужно: `tegrako` — это то, чем чинишь бота, когда он **не отвечает** и Telegram-админка бесполезна.

---

## Вебхуки Remnawave

Бот слушает события панели на порту `9090`. Remnawave шлёт `POST /webhook` с заголовком `X-Webhook-Secret`.

В `/opt/remnawave/.env`:

```env
WEBHOOK_ENABLED=true
WEBHOOK_URL=http://tegrakobot:9090/webhook
WEBHOOK_SECRET_HEADER=<тот же секрет что в WEBHOOK_SECRET бота>
```

Обрабатываемые события: `user.expired`, `user.limited`, `user.disabled`, `user.expires_in_24/48/72_hours`, `torrent_blocker.report`.

Проверка живости: `GET /health` → `{"status": "ok"}`.

---

## Бэкапы БД

Контейнер `tegrakobot-backup` каждые `BACKUP_INTERVAL_SECONDS` (по умолчанию 6ч) снимает `pg_dump` в `./backups/`, хранит `BACKUP_RETENTION_DAYS` дней (по умолчанию 14). Папка вне docker-volume — стоит рутинно синкать её (`rsync`/`restic`) на другой диск или хост, т.к. сама по себе она не защищает от потери диска CT102.

Ручной бэкап, восстановление и снос БД — через `tegrako` (пункты 6/7/8), не руками.

---

## Обновление

```bash
tegrako
# → 4) Обновление
```

Тянет стабильный тег, накатывает недостающие миграции из `migrations/`, пересобирает контейнеры, обновляет саму команду `tegrako`. Ручные `git pull && docker compose up -d --build` по-прежнему работают, но не применяют миграции автоматически.

---

## Логи и диагностика

```bash
tegrako
# → 3) Логи, → 9) Диагностика
```

Диагностика проверяет DNS внутри контейнера бота, доступность панели/telemt, здоровье БД, свободную память/диск и расхождения `git status` — прежде чем лезть в SSH руками.

---

## Релиз новой версии (для разработки)

```bash
export GH_TOKEN=ghp_...   # короткоживущий PAT с правами repo
./release.sh v1.5.0
```

Бампает `VERSION`, коммитит, двигает теги `v1.5.0` и `stable`, обновляет ассет `install.sh` в GitHub Release. Пока релиз не сделан — `main` может содержать недоделанный функционал, пользователи через `install.sh`/`tegrako update` его не увидят: они всегда тянут `stable`.

Каждая новая версия схемы БД, требующая `ALTER TABLE`, кладётся в `migrations/NNN_описание.sql` (идемпотентно, `IF NOT EXISTS`) — `tegrako` → «Обновление» накатывает её сам.

---

## Диагностика

**Бот не отвечает** — проверь `BOT_TOKEN`, контейнер запущен (`docker ps`), сеть доступна.

**502 от панели** — панель ещё поднимается, подожди 30 сек, проверь `docker logs remnawave`.

**Вебхук возвращает 403** — секрет в боте и панели не совпадают.

**Ошибка БД** — проверь `DATABASE_URL` и что контейнер `tegrakobot-db` жив.

---

## License

[GLWTPL](https://github.com/me-shaon/GLWTPL/)
