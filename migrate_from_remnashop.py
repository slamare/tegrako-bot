"""
Скрипт одноразовой миграции пользователей из remnashop (PostgreSQL) в tegrabot (PostgreSQL).

Запускать ОДИН РАЗ перед удалением remnashop:

    python migrate_from_remnashop.py

Что переносится из remnashop:
  users:
    - telegram_id
    - username
    - is_blocked → is_banned

  subscriptions (текущая подписка пользователя):
    - user_remna_id  → remnawave_uuid
    - username берём из Remnawave API по uuid

Требования:
  - Заполненный .env tegrabot (DATABASE_URL, PANEL_API_URL, PANEL_API_KEY)
  - Доступ к БД remnashop (REMNASHOP_DB_URL в .env)
    Пример: postgresql://remnashop:PASSWORD@127.0.0.1:5001/remnashop
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

REMNASHOP_DB_URL = os.getenv("REMNASHOP_DB_URL", "")


async def migrate():
    if not REMNASHOP_DB_URL:
        print("❌ Укажи REMNASHOP_DB_URL в .env")
        print("   Пример: postgresql://remnashop:PASSWORD@127.0.0.1:5001/remnashop")
        sys.exit(1)

    from db.database import init_db, create_tables, async_session_maker
    from db import dal
    from config.settings import settings
    from bot.services.remnawave import get_sdk

    # ── Читаем пользователей из remnashop ─────────────────────────────────────
    print(f"🔍 Подключаюсь к remnashop БД...")

    import asyncpg
    # asyncpg не понимает +asyncpg в схеме — убираем если есть
    raw_url = REMNASHOP_DB_URL.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(raw_url)

    rows = await conn.fetch("""
        SELECT
            u.telegram_id,
            u.username,
            u.is_blocked,
            s.user_remna_id::text AS remnawave_uuid
        FROM users u
        LEFT JOIN subscriptions s ON s.id = u.current_subscription_id
        ORDER BY u.id
    """)
    await conn.close()

    print(f"📋 Найдено пользователей в remnashop: {len(rows)}")

    if not rows:
        print("⚠️  Нет пользователей для миграции.")
        return

    # ── Инициализируем tegrabot БД ─────────────────────────────────────────────
    init_db(settings.DATABASE_URL)
    await create_tables()

    migrated = skipped = errors = 0
    sdk = get_sdk()

    async with async_session_maker() as session:
        for row in rows:
            tg_id      = row["telegram_id"]
            tg_username = row["username"]
            is_blocked  = row["is_blocked"]
            rw_uuid     = row["remnawave_uuid"]  # None если нет подписки

            try:
                # Пропускаем если уже есть в tegrabot
                existing = await dal.get_user(session, tg_id)
                if existing:
                    print(f"  ⏭  Пропускаю {tg_id} (@{tg_username}) — уже есть")
                    skipped += 1
                    continue

                # Получаем username из Remnawave если есть uuid
                rw_username = None
                if rw_uuid:
                    try:
                        rw_user = await sdk.users.get_user_by_uuid(rw_uuid)
                        if rw_user:
                            rw_username = rw_user.username
                    except Exception as e:
                        print(f"  ⚠️  Remnawave API недоступен для {tg_id}: {e}")

                # Fallback: используем tg username
                if not rw_username:
                    rw_username = tg_username

                # Записываем в tegrabot БД
                await dal.create_user(session, tg_id, username=tg_username)
                await dal.update_user(
                    session,
                    tg_id,
                    remnawave_username=rw_username,
                    remnawave_uuid=rw_uuid,
                    is_registered=True,
                    is_banned=bool(is_blocked),
                )

                status = f"uuid={rw_uuid[:8]}..." if rw_uuid else "без подписки"
                ban = " [BANNED]" if is_blocked else ""
                print(f"  ✅ {tg_id} (@{tg_username}) → {rw_username} {status}{ban}")
                migrated += 1

            except Exception as e:
                print(f"  ❌ Ошибка для {tg_id}: {e}")
                errors += 1

    print()
    print("─" * 50)
    print(f"✅ Мигрировано:  {migrated}")
    print(f"⏭  Пропущено:   {skipped}")
    print(f"❌ Ошибок:       {errors}")
    print("─" * 50)

    if errors == 0:
        print("\n🎉 Миграция завершена успешно!")
        print("   Можно останавливать remnashop:")
        print("   cd /opt/remnashop && docker compose down")
    else:
        print("\n⚠️  Были ошибки. Проверь вывод выше перед удалением remnashop.")


if __name__ == "__main__":
    asyncio.run(migrate())
