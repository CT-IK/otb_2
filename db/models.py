
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Boolean, ForeignKey, Text
# Модель для хранения временных слотов
from sqlalchemy import Date, Time

class Base(DeclarativeBase):
    pass


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    vk_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tg_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), nullable=True)



class Faculty(Base):
    __tablename__ = "faculties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    google_sheet_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    admin: Mapped["User"] = relationship("User", foreign_keys=[admin_id], backref="admin_faculties")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=True)
    tg_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=True)
    is_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sobeser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin_faculty: Mapped[bool] = mapped_column(Boolean, default=False)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), nullable=True)

class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[Date] = mapped_column(Date, nullable=False)
    start_time: Mapped[Time] = mapped_column(Time, nullable=False)
    end_time: Mapped[Time] = mapped_column(Time, nullable=False)

# Таблица доступности собеседующих по временным слотам
class Availability(Base):
    __tablename__ = "availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), nullable=False)

    date: Mapped[str] = mapped_column(String(20), nullable=False)  # формат: DD.MM(день)
    time_slot: Mapped[str] = mapped_column(String(20), nullable=False)  # например, "10:00 - 11:00"
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

# Таблица лимитов слотов на дату и время
class SlotLimit(Base):
    __tablename__ = "slot_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), nullable=False)
    date: Mapped[str] = mapped_column(String(20), nullable=False)
    time_slot: Mapped[str] = mapped_column(String(20), nullable=False)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    

class InterviewRegistration(Base):
    __tablename__ = "interview_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"), nullable=False)
    date: Mapped[str] = mapped_column(String(20), nullable=False)
    time_slot: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    canceled: Mapped[bool] = mapped_column(Boolean, default=False)