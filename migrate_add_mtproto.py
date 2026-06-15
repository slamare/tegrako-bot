#!/usr/bin/env python3
"""
Добавляет колонку mtproto_secret в таблицу users.
Запускать: docker exec tegrakobot python3 migrate_add_mtproto.py
"""
import asyncio
from db.database import init_db
from config.settings import settings


async def run():
    init_db(settings.DATABASE_URL)
    from db.database import engine
    from sqlalchemy import text

    async with engine.begin() as conn:
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='users' AND column_name='mtproto_secret'
        """))
        if result.fetchone():
            print("\u2705 Column mtproto_secret already exists.")
            return
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN mtproto_secret VARCHAR(32) NULL"
        ))
        print("\u2705 Column mtproto_secret added.")


if __name__ == "__main__":
    asyncio.run(run())
