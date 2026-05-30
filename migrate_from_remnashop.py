import asyncio
import asyncpg
import httpx
from config.settings import settings


REMNASHOP_DB_URL = (
    "postgresql://remnashop:8d25a07e704851529b2025c13d3989f08fb0c5131c07d314"
    "@remnashop-db:5432/remnashop"
)


async def migrate():
    from db.database import init_db
    from db import dal
    import db.database as _db

    init_db(settings.DATABASE_URL)

    print("🔍 Подключаюсь к remnashop БД...")
    conn = await asyncpg.connect(REMNASHOP_DB_URL, ssl=False)

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

    print("📡 Загружаю пользователей из панели Remnawave...")

    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.get(
            f"{settings.PANEL_API_URL}/api/users?limit=1000",
            headers={"Authorization": f"Bearer {settings.PANEL_API_KEY}"},
            timeout=15,
        )
        panel_users = resp.json().get("response", {}).get("users", [])

    panel_by_uuid = {u["uuid"]: u for u in panel_users}
    print(f"📡 В панели найдено: {len(panel_users)} пользователей")

    migrated = skipped = errors = 0

    async with _db.async_session_maker() as session:
        for row in rows:
            tg_id = row["telegram_id"]
            tg_username = row["username"]
            is_blocked = row["is_blocked"]
            rw_uuid = row["remnawave_uuid"]

            try:
                existing = await dal.get_user(session, tg_id)
                if existing:
                    print(f"  ⏭ Пропускаю {tg_id} (@{tg_username}) — уже есть")
                    skipped += 1
                    continue

                # UUID существует в панели → берём актуальный username
                rw_username = tg_username
                if rw_uuid and rw_uuid in panel_by_uuid:
                    rw_username = panel_by_uuid[rw_uuid]["username"]

                # 🔒 ЖЁСТКАЯ ЗАЩИТА ОТ ПЕРЕПРИВЯЗКИ UUID
                existing_by_uuid = await dal.get_user_by_uuid(session, rw_uuid)
                if existing_by_uuid and existing_by_uuid.telegram_id != tg_id:
                    print(
                        f"⚠️ UUID {rw_uuid} уже привязан к "
                        f"{existing_by_uuid.telegram_id}, пропуск {tg_id}"
                    )
                    skipped += 1
                    continue

                # создаём пользователя
                await dal.create_user(session, tg_id, username=tg_username)

                # обновляем данные
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

    print("\n" + "─" * 50)
    print(f"✅ Мигрировано: {migrated}")
    print(f"⏭ Пропущено:   {skipped}")
    print(f"❌ Ошибок:      {errors}")

    if errors == 0:
        print("\n🎉 Миграция завершена успешно!")


if __name__ == "__main__":
    asyncio.run(migrate())