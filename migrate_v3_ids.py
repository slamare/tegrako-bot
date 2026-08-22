"""
Разовый скрипт после апгрейда Remnawave панели на v3.x.x.
Панель больше не отдаёт uuid пользователей — только числовой id.
Старые remnawave_uuid в БД бота больше не валидны, пересчитывает их через username.

Запускать один раз сразу после обновления панели: docker exec tegrakobot python3 migrate_v3_ids.py
"""
import asyncio
from config.settings import settings
from db.database import init_db
from bot.services import remnawave


async def run():
    init_db(settings.DATABASE_URL)

    import db.database as _db
    from db import dal

    async with _db.async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        print(f"Пользователей в боте: {len(users)}")

        updated = skipped = not_found = 0
        for user in users:
            if not user.remnawave_username:
                skipped += 1
                continue
            resp = await remnawave._get_client().get(
                remnawave._url(f"/users/by-username/{user.remnawave_username}"),
                headers=remnawave._headers(),
            )
            if resp.status_code != 200:
                print(f"  ❌ Не найден в панели: {user.remnawave_username} ({user.telegram_id})")
                not_found += 1
                continue
            data = resp.json().get("response", resp.json())
            new_id = str(data["id"])
            if user.remnawave_uuid == new_id:
                skipped += 1
                continue
            await dal.update_user(session, user.telegram_id, remnawave_uuid=new_id)
            print(f"  ✅ {user.telegram_id} (@{user.username}) → {user.remnawave_username} id={new_id}")
            updated += 1

    print(f"\n✅ Обновлено: {updated} | ⏭ Пропущено: {skipped} | ❌ Не найдено: {not_found}")


if __name__ == "__main__":
    asyncio.run(run())
