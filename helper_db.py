# helper_db.py
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

async def init_db_async():
    """Асинхронная инициализация БД с правильными настройками из docker-compose"""
    # Правильные настройки из вашего docker-compose
    DB_USER = os.getenv("DB_USER", "zapis_user")  # ← ИЗМЕНИТЕ НА zapis_user
    DB_PASSWORD = os.getenv("DB_PASSWORD", "zapis_pass")
    DB_HOST = os.getenv("DB_HOST", "postgres")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "zapis")
    
    # Асинхронный URL для asyncpg
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    try:
        print(f"🔗 Подключаемся к базе данных: {DB_HOST}:{DB_PORT}/{DB_NAME}")
        print(f"👤 Пользователь: {DB_USER}")
        
        # Создаем асинхронный движок
        engine = create_async_engine(
            DATABASE_URL,
            echo=True,
            future=True
        )
        
        # Проверяем подключение
        async with engine.begin() as conn:
            print("✅ Подключение к PostgreSQL установлено")
            
        # Создаем все таблицы
        async with engine.begin() as conn:
            print("🗃️ Создаем таблицы...")
            from models import Base
            await conn.run_sync(Base.metadata.create_all)
        
        print("✅ Все таблицы успешно созданы!")
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы данных: {e}")
        return False

async def check_database_connection():
    """Проверка подключения к базе данных"""
    DB_USER = os.getenv("DB_USER", "zapis_user")  # ← ИЗМЕНИТЕ НА zapis_user
    DB_PASSWORD = os.getenv("DB_PASSWORD", "zapis_pass")
    DB_HOST = os.getenv("DB_HOST", "postgres")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "zapis")
    
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    try:
        engine = create_async_engine(DATABASE_URL)
        async with engine.begin() as conn:
            result = await conn.execute("SELECT version();")
            version = result.scalar()
            print(f"📋 Версия PostgreSQL: {version}")
            
            result = await conn.execute("SELECT current_database();")
            db_name = result.scalar()
            print(f"📁 Текущая база данных: {db_name}")
            
            result = await conn.execute("SELECT current_user;")
            user = result.scalar()
            print(f"👤 Текущий пользователь: {user}")
            
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Не удалось подключиться к базе данных: {e}")
        return False

async def main():
    """Основная асинхронная функция"""
    print("🚀 Запуск асинхронной инициализации базы данных...")
    print("=" * 50)
    
    # Проверяем подключение
    if not await check_database_connection():
        print("\n💡 Проверьте настройки подключения в .env файле")
        return
    
    print("\n🗃️ Начинаем инициализацию таблиц...")
    
    # Инициализируем базу данных
    success = await init_db_async()
    
    if success:
        print("\n🎉 База данных успешно инициализирована!")
        print("📊 Все таблицы созданы!")
    else:
        print("\n💥 Инициализация завершена с ошибками!")

if __name__ == "__main__":
    asyncio.run(main())