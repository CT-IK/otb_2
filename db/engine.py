import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from dotenv import load_dotenv
load_dotenv()


engine = create_async_engine(os.getenv('DB_URL'), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
