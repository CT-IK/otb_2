from datetime import date, time, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from db.models import Slot, Base
import os 
from dotenv import load_dotenv
load_dotenv()

# Укажи свой URL подключения к PostgreSQL
DATABASE_URL = os.getenv('DB_URL')

dates = [
    date(2025, 9, 26),
    date(2025, 9, 27),
    date(2025, 9, 28),
    date(2025, 9, 29),
    date(2025, 9, 30),
    date(2025, 10, 1),
    date(2025, 10, 2),
    date(2025, 10, 3),
]

time_slots = [
    (time(10, 0), time(11, 0)),
    (time(11, 0), time(12, 0)),
    (time(12, 0), time(13, 0)),
    (time(13, 0), time(14, 0)),
    (time(14, 0), time(15, 0)),
    (time(15, 0), time(16, 0)),
    (time(16, 0), time(17, 0)),
    (time(17, 0), time(18, 0)),
    (time(18, 0), time(19, 0)),
    (time(19, 0), time(20, 0)),
    (time(20, 0), time(21, 0)),
    (time(21, 0), time(22, 0)),
]

def main():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for d in dates:
            for start, end in time_slots:
                slot = Slot(date=d, start_time=start, end_time=end)
                session.add(slot)
        session.commit()
    print("Слоты успешно добавлены!")

if __name__ == "__main__":
    main()
