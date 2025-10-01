# helper_db.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

async def init_db_async():
    """Асинхронная инициализация БД"""
    DB_USER = "zapis_user"
    DB_PASSWORD = "zapis_pass" 
    DB_HOST = "postgres"
    DB_PORT = "5432"
    DB_NAME = "zapis"
    
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    try:
        print(f"🔗 Подключаемся к: {DB_HOST}:{DB_PORT}/{DB_NAME}")
        
        engine = create_async_engine(DATABASE_URL, echo=True)
        
        # Проверяем подключение
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            test = result.scalar()
            print(f"✅ Проверка подключения: SELECT 1 = {test}")
            
        # Создаем таблицы
        async with engine.begin() as conn:
            print("🗃️ Создаем таблицы...")
            from db.models import Base
            await conn.run_sync(Base.metadata.create_all)
        
        print("✅ Все таблицы созданы!")
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def main():
    print("🚀 Запуск инициализации БД...")
    success = await init_db_async()
    
    if success:
        print("\n🎉 База данных готова!")
    else:
        print("\n💥 Ошибка!")

if __name__ == "__main__":
    asyncio.run(main())