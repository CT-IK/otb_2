# seed_data_async.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

async def seed_sample_data_async():
    """Асинхронное добавление тестовых данных в существующую базу"""
    DB_USER = "zapis_user"
    DB_PASSWORD = "zapis_pass"
    DB_HOST = "postgres" 
    DB_PORT = "5432"
    DB_NAME = "zapis"
    
    DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    try:
        print(f"🔗 Подключаемся к базе: {DB_HOST}:{DB_PORT}/{DB_NAME}")
        
        engine = create_async_engine(DATABASE_URL)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        from db.models import Faculty, User, Candidate, FacultyTimeDelta
        
        print("📊 Добавляем тестовые данные...")
        
        async with async_session() as session:
            # 1. Создаем факультеты БЕЗ admin_id
            faculties = [
                Faculty(name="Факультет компьютерных наук", google_sheet_url="https://docs.google.com/spreadsheets/..."),
                Faculty(name="Факультет экономики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
                Faculty(name="Факультет математики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
            ]
            
            session.add_all(faculties)
            await session.commit()
            print("✅ Факультеты созданы")
            
            # Обновляем объекты чтобы получить ID
            for faculty in faculties:
                await session.refresh(faculty)
            
            # 2. Создаем администратора
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
            print("✅ Админ создан")
            
            # 3. Обновляем факультет с admin_id
            faculties[0].admin_id = admin.id
            await session.commit()
            print("✅ Связь факультет-админ установлена")
            
            # 4. Создаем дельта-время для факультетов
            for faculty in faculties:
                time_delta = FacultyTimeDelta(
                    faculty_id=faculty.id,
                    hours_before_interview=4
                )
                session.add(time_delta)
            
            await session.commit()
            print("✅ Временные дельты созданы")
            
            # 5. Создаем тестовых кандидатов
            candidates = [
                Candidate(
                    first_name="Иван",
                    last_name="Петров", 
                    vk_id="vk12345",
                    tg_id="tg12345",
                    faculty_id=faculties[0].id
                ),
                Candidate(
                    first_name="Мария",
                    last_name="Сидорова",
                    vk_id="vk67890", 
                    tg_id="tg67890",
                    faculty_id=faculties[1].id
                ),
            ]
            
            session.add_all(candidates)
            await session.commit()
            print("✅ Кандидаты созданы")
            
            # 6. Создаем обычных пользователей
            users = [
                User(
                    first_name="Алексей",
                    last_name="Собеседователь",
                    tg_id="sobes1",
                    is_sobeser=True,
                    faculty_id=faculties[0].id
                ),
                User(
                    first_name="Елена",
                    last_name="Интервьюер",
                    tg_id="sobes2", 
                    is_sobeser=True,
                    faculty_id=faculties[1].id
                ),
            ]
            
            session.add_all(users)
            await session.commit()
            print("✅ Собеседующие созданы")
            
            print("\n🎉 Все тестовые данные успешно добавлены!")
            print("\n📋 Добавлено:")
            print(f"   - Факультеты: {len(faculties)}")
            print(f"   - Администратор: 1")
            print(f"   - Кандидаты: {len(candidates)}")
            print(f"   - Собеседующие: {len(users)}")
        
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при добавлении данных: {e}")
        return False

async def main():
    print("🚀 Запуск добавления тестовых данных...")
    if await seed_sample_data_async():
        print("\n🎉 База данных наполнена тестовыми данными!")
    else:
        print("\n💥 Ошибка добавления данных!")

if __name__ == "__main__":
    asyncio.run(main())