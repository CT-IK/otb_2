from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker
from db.models import Base, Faculty, User, Candidate, Slot, Availability, SlotLimit, InterviewRegistration, FacultyTimeDelta
import os

# Данные для подключения к PostgreSQL (замените на свои)
DB_USER = os.getenv("DB_USER", "zapis")
DB_PASSWORD = os.getenv("DB_PASSWORD", "zapis_pass")
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "zapis")

# Создание строки подключения
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def init_database():
    """Инициализация базы данных и создание таблиц"""
    try:
        # Создаем движок
        engine = create_engine(DATABASE_URL)
        
        # Создаем все таблицы
        Base.metadata.create_all(bind=engine)
        
        print("✅ База данных успешно инициализирована!")
        print("✅ Все таблицы созданы:")
        for table in Base.metadata.tables.keys():
            print(f"   - {table}")
            
        return engine
        
    except Exception as e:
        print(f"❌ Ошибка при инициализации базы данных: {e}")
        return None

def create_sample_data(engine):
    """Создание тестовых данных"""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Создаем факультеты
        faculties = [
            Faculty(name="Факультет компьютерных наук", google_sheet_url="https://docs.google.com/spreadsheets/..."),
            Faculty(name="Факультет экономики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
            Faculty(name="Факультет математики", google_sheet_url="https://docs.google.com/spreadsheets/..."),
        ]
        
        for faculty in faculties:
            db.add(faculty)
        db.commit()
        
        # Обновляем объекты, чтобы получить их ID
        for faculty in faculties:
            db.refresh(faculty)
        
        # Создаем администратора
        admin = User(
            first_name="Админ",
            last_name="Факультетский", 
            tg_id="admin123",
            is_admin_faculty=True,
            faculty_id=faculties[0].id
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        
        # Обновляем факультет с администратором
        faculties[0].admin_id = admin.id
        db.commit()
        
        # Создаем дельта-время для факультетов
        for faculty in faculties:
            time_delta = FacultyTimeDelta(
                faculty_id=faculty.id,
                hours_before_interview=4
            )
            db.add(time_delta)
        
        db.commit()
        print("✅ Тестовые данные успешно добавлены!")
        
    except Exception as e:
        print(f"❌ Ошибка при добавлении тестовых данных: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("🚀 Начинаем инициализацию базы данных...")
    
    # Инициализируем базу данных
    engine = init_database()
    
    if engine:
        # Создаем тестовые данные (опционально)
        create_sample_data(engine)
        
        print("\n🎉 Инициализация завершена успешно!")
        print("📊 База данных готова к использованию.")