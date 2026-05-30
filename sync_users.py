"""
Синхронизация пользователей бота с панелью Remnawave.
Проставляет remnawave_uuid всем пользователям у которых он пустой.
Запускать: docker exec tegrakobot python3 sync_users.py
"""
import asyncio
import httpx
from config.settings import settings
from db.database import init_db, create_tables


def _headers():
    return {"Authorization": f"Bearer {settings.PANEL_API_KEY}"}


async def get_all_panel_users() -> list[dict]:
    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.get(
            f"{settings.PANEL_API_URL}/api/users?limit=1000",
            headers=_headers(), timeout=15
        )
        data = resp.json()
        return data.get("response", {}).get("users", [])


async def sync():
    init_db(settings.DATABASE_URL)
    await create_tables()

    import db.database as _db
    from db import dal

    print("Загружаем пользователей из панели...")
    panel_users = await get_all_panel_users()
    panel_by_tg = {u["telegramId"]: u for u in panel_users if u.get("telegramId")}
    print(f"В панели найдено пользователей с telegram_id: {len(panel_by_tg)}")

    async with _db.async_session_maker() as session:
        bot_users = await dal.get_all_users(session, only_registered=True)
        print(f"В боте зарегистрировано: {len(bot_users)}")

        updated = skipped = not_found = 0
        for user in bot_users:
            if user.remnawave_uuid:
                skipped += 1
                continue

            panel_user = panel_by_tg.get(user.telegram_id)
            if not panel_user:
                print(f"  ❌ Не найден в панели: @{user.username} ({user.telegram_id})")
                not_found += 1
                continue

            await dal.update_user(
                session,
                user.telegram_id,
                remnawave_uuid=panel_user["uuid"],
                remnawave_username=panel_user["username"],
            )
            print(f"  ✅ {user.telegram_id} (@{user.username}) → {panel_user['username']} [{panel_user['uuid'][:8]}...]")
            updated += 1

    print(f"\n─────────────────────────")
    print(f"✅ Обновлено:    {updated}")
    print(f"⏭  Пропущено:   {skipped} (uuid уже был)")
    print(f"❌ Не найдено:  {not_found}")


if __name__ == "__main__":
    asyncio.run(sync())
