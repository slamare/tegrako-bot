from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from db.models import Base

engine = None
async_session_maker = None


def init_db(database_url: str):
    global engine, async_session_maker
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_timeout=30,
        pool_recycle=1800,
    )
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
