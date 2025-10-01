# helper_db.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

async def init_db_async():
    """Асинхронная инициализация БД с прямыми настройками"""
    # Прямые настройки из docker-compose
    DB_USER = "zapis_user"
    DB_PASSWORD = "zapis_pass"
    DB_HOST = "postgres"
    DB_PORT = "5432"
    DB_NAME = "zapis"
    
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
            from db.models import Base
            await conn.run_sync(Base.metadata.create_all)
        
        print("✅ Все таблицы успешно созданы!")
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы данных: {e}")
        return False

async def check_database_connection():
    """Проверка подключения к базе данных"""
    DB_USER = "zapis_user"
    DB_PASSWORD = "zapis_pass"
    DB_HOST = "postgres"
    DB_PORT = "5432"
    DB_NAME = "zapis"
    
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    try:
        engine = create_async_engine(DATABASE_URL)
        async with engine.begin() as conn:
            # Простая проверка подключения
            result = await conn.execute("SELECT 1")
            test = result.scalar()
            print(f"✅ Проверка подключения: SELECT 1 = {test}")
            
        await engine.dispose()
        return True
    except Exception as e:
        print(f"❌ Не удалось подключиться к базе данных: {e}")
        return False

async def create_sample_data_async():
    """Асинхронное создание тестовых данных"""
    DB_USER = "zapis_user"
    DB_PASSWORD = "zapis_pass"
    DB_HOST = "postgres"
    DB_PORT = "5432"
    DB_NAME = "zapis"
    
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            from db.models import Faculty, FacultyTimeDelta, User
            
            print("📊 Добавляем тестовые данные...")
            
            # Создаем факультеты
            faculties = [
                Faculty(name="Факультет компьютерных наук", google_sheet_url="https://docs.google.com/spreadsheets/..."),
                Faculty(name="Факультет экономики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
                Faculty(name="Факультет математики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
            ]
            
            session.add_all(faculties)
            await session.commit()
            
            # Обновляем объекты, чтобы получить их ID
            for faculty in faculties:
                await session.refresh(faculty)
            
            # Создаем тестового администратора
            admin = User(
                first_name="Админ",
                last_name="Системы", 
                tg_id="admin123",
                is_admin_faculty=True,
                faculty_id=faculties[0].id
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)
            
            # Обновляем факультет с администратором
            faculties[0].admin_id = admin.id
            await session.commit()
            
            # Создаем дельта-время для факультетов
            for faculty in faculties:
                time_delta = FacultyTimeDelta(
                    faculty_id=faculty.id,
                    hours_before_interview=4
                )
                session.add(time_delta)
            
            await session.commit()
            print("✅ Тестовые данные успешно добавлены!")
            
    except Exception as e:
        print(f"❌ Ошибка при добавлении тестовых данных: {e}")
    finally:
        await engine.dispose()

async def main():
    """Основная асинхронная функция"""
    print("🚀 Запуск асинхронной инициализации базы данных...")
    print("=" * 50)
    
    # Проверяем подключение
    if not await check_database_connection():
        return
    
    print("\nНачинаем инициализацию таблиц...")
    
    # Инициализируем базу данных
    success = await init_db_async()
    
    if success:
        print("\n📊 Добавляем тестовые данные...")
        await create_sample_data_async()
        print("\n🎉 База данных успешно инициализирована!")
        print("📊 Все таблицы созданы и наполнены тестовыми данными!")
    else:
        print("\n💥 Инициализация завершена с ошибками!")

if __name__ == "__main__":
    asyncio.run(main())