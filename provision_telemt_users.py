#!/usr/bin/env python3
"""
Прогоняет существующих пользователей с подпиской через telemt:
генерирует секрет, добавляет в telemt.toml, сохраняет в БД.

Запускать: docker exec tegrakobot python3 provision_telemt_users.py
"""
import asyncio
import logging
from db.database import init_db
from config.settings import settings
from bot.services import telemt

logging.basicConfig(level=logging.INFO)


async def run():
    init_db(settings.DATABASE_URL)

    import db.database as _db
    from db import dal
    from sqlalchemy import update
    from db.models import User

    async with _db.async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        print(f"Зарегистрировано пользователей: {len(users)}")

        added = skipped = errors = 0

        for user in users:
            if not user.remnawave_uuid or not user.remnawave_username:
                skipped += 1
                continue

            if user.mtproto_secret:
                if not telemt.user_exists(user.remnawave_username):
                    try:
                        telemt.add_user(user.remnawave_username, user.mtproto_secret)
                        print(f"  \U0001f527 Restored in config: {user.remnawave_username}")
                    except Exception as e:
                        print(f"  \u274c Config error for {user.remnawave_username}: {e}")
                else:
                    print(f"  \u23ed\ufe0f  Already exists: {user.remnawave_username}")
                skipped += 1
                continue

            try:
                secret = telemt.generate_secret()
                telemt.add_user(user.remnawave_username, secret)
                await session.execute(
                    update(User)
                    .where(User.telegram_id == user.telegram_id)
                    .values(mtproto_secret=secret)
                )
                await session.commit()
                print(f"  \u2705 {user.remnawave_username} ({user.telegram_id})")
                added += 1
            except Exception as e:
                print(f"  \u274c Error for {user.remnawave_username}: {e}")
                errors += 1

    print(f"\n{'─' * 40}")
    print(f"\u2705 Added:   {added}")
    print(f"\u23ed\ufe0f  Skipped: {skipped}")
    print(f"\u274c Errors:  {errors}")


if __name__ == "__main__":
    asyncio.run(run())
