
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram import F
import asyncio
from db.engine import get_session
from db.models import User, Faculty, Candidate, Availability, SlotLimit, InterviewRegistration, FacultyTimeDelta
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv
import gspread
import traceback
import redis.asyncio as redis
import datetime

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

redis_client = None

async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url("redis://redis:6379", encoding="utf-8", decode_responses=True)
    return redis_client

async def get_faculty_time_delta(session, faculty_id):
    """Получает время блокировки слотов для факультета"""
    result = await session.execute(
        select(FacultyTimeDelta.hours_before_interview).where(
            FacultyTimeDelta.faculty_id == faculty_id
        )
    )
    hours = result.scalar()
    return hours if hours is not None else 4  # По умолчанию 4 часа


class VKAuth(StatesGroup):
    waiting_vk_id = State()

class InterviewFSM(StatesGroup):
    choosing_date = State()
    choosing_time = State()

class CancelFSM(StatesGroup):
    waiting_reason = State()


# --- VK ID: старт, подтверждение, отказ ---
@dp.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user and (user.is_admin_faculty or user.is_sobeser):
            await message.answer("Вы уже авторизованы как сотрудник.")
            return
    await message.answer(
        "Пожалуйста, введите ваш VK ID для регистрации.\n\n"
        "<b>VK ID</b> — это числовой идентификатор вашей страницы ВКонтакте.\n"
        "Его можно узнать, открыв свой профиль в VK и посмотрев на адрес страницы: https://vk.com/id123456\n"
        "Например, если адрес https://vk.com/id123456, то ваш VK ID — <b>123456</b>."
    )
    await state.set_state(VKAuth.waiting_vk_id)

@dp.message(VKAuth.waiting_vk_id)
async def vk_id_handler(message: types.Message, state: FSMContext):
    vk_id = message.text.strip()
    async for session in get_session():
        candidate = await session.scalar(select(Candidate).where(Candidate.vk_id == vk_id))
        if not candidate:
            await message.answer("Пользователь с таким VK ID не найден. Попробуйте ещё раз:")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data=f"vk_yes_{candidate.id}"),
             InlineKeyboardButton(text="Нет", callback_data="vk_no")]
        ])
        await message.answer(
            f"Пользователь {candidate.first_name} {candidate.last_name} найден!\nЭто вы?",
            reply_markup=kb
        )
        await state.clear()

@dp.callback_query(F.data.startswith("vk_yes_"))
async def vk_yes_callback(call: CallbackQuery):
    candidate_id = int(call.data.split("_")[-1])
    async for session in get_session():
        candidate = await session.scalar(select(Candidate).where(Candidate.id == candidate_id))
        if not candidate:
            await call.message.answer("Ошибка: кандидат не найден.")
            return
        user = await session.scalar(select(User).where(User.tg_id == str(call.from_user.id)))
        if not user:
            user = User(
                tg_id=str(call.from_user.id),
                first_name=candidate.first_name,
                last_name=candidate.last_name,
                faculty_id=candidate.faculty_id,
                is_candidate=True
            )
            session.add(user)
            await session.commit()
        else:
            # Обновляем is_candidate, если нужно
            if not user.is_candidate:
                user.is_candidate = True
                await session.commit()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Записаться на собеседование", callback_data="register_interview")]
        ]
    )
    await call.message.answer("Вы успешно зарегистрированы!", reply_markup=kb)
    await call.message.edit_reply_markup()

@dp.callback_query(F.data == "vk_no")
async def vk_no_callback(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Пожалуйста, введите ваш VK ID ещё раз:")
    await call.message.edit_reply_markup()
    await state.set_state(VKAuth.waiting_vk_id)



# --- Меню кандидата ---
@dp.message(Command("menu"))
async def candidate_menu(message: types.Message):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await message.answer("Вы не зарегистрированы как кандидат.")
            return
        reg = await session.scalar(
            select(InterviewRegistration).where(
                InterviewRegistration.user_id == user.id,
                InterviewRegistration.canceled == False
            )
        )
        if reg:
            text = (
                f"<b>Вы уже записаны на собеседование:</b>\n"
                f"\n<b>Дата:</b> {reg.date}"
                f"\n<b>Время:</b> {reg.time_slot}"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Отменить запись", callback_data="cancel_interview")]
                ]
            )
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
        else:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Записаться на собеседование", callback_data="register_interview")]
                ]
            )
            await message.answer("Меню кандидата:", reply_markup=kb)

# --- Кнопка записи кандидата ---
@dp.callback_query(F.data == "register_interview")
async def register_interview_start_callback(callback: CallbackQuery, state: FSMContext):
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        faculty_id = user.faculty_id
        now = datetime.datetime.now()
        result = await session.execute(
            select(SlotLimit.date).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.limit > 0
            ).distinct()
        )
        # Получаем время блокировки для факультета
        hours_delta = await get_faculty_time_delta(session, faculty_id)
        
        # Фильтруем даты: только те, до которых больше установленного времени
        dates = []
        for r in result.all():
            try:
                slot_date = r[0]
                if len(slot_date) >= 5 and slot_date[2] == '.':
                    day, month = slot_date[:2], slot_date[3:5]
                    year = str(now.year)
                    slot_dt = datetime.datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
                else:
                    slot_dt = datetime.datetime.fromisoformat(slot_date)
                # Если дата сегодня или позже, и до неё больше установленного времени
                if slot_dt.date() > now.date() or (slot_dt.date() == now.date() and (slot_dt - now) >= datetime.timedelta(hours=hours_delta)):
                    dates.append(slot_date)
            except Exception:
                continue
        if not dates:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_menu")]
                ]
            )
            await callback.message.edit_text("Нет доступных дат для записи.", reply_markup=kb)
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=date, callback_data=f"reg_date:{date}")] for date in dates
            ] + [
                [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_menu")]
            ]
        )
        await callback.message.edit_text("Выберите дату для собеседования:", reply_markup=kb)
        await state.set_state(InterviewFSM.choosing_date)
# --- Обработчик кнопки 'Назад' на этапе выбора даты ---
@dp.callback_query(InterviewFSM.choosing_date, F.data == "reg_back_to_menu")
async def register_interview_back_to_menu(callback: CallbackQuery, state: FSMContext):
    # Просто возвращаем меню кандидата
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Записаться на собеседование", callback_data="register_interview")]
            ]
        )
        await callback.message.edit_text("Меню кандидата:", reply_markup=kb)
        await state.clear()


@dp.callback_query(InterviewFSM.choosing_date, F.data.startswith("reg_date:"))
async def register_interview_choose_time(callback: CallbackQuery, state: FSMContext):
    date = callback.data.split(":", 1)[1]
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        faculty_id = user.faculty_id
        now = datetime.datetime.now()
        # Получаем доступные интервалы времени с лимитом > 0 и фильтруем по 4 часам
        result = await session.execute(
            select(SlotLimit.time_slot, SlotLimit.date).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.date == date,
                SlotLimit.limit > 0
            )
        )
        time_slots = []
        for r in result.all():
            ts, slot_date = r
            try:
                if len(slot_date) >= 5 and slot_date[2] == '.':
                    day, month = slot_date[:2], slot_date[3:5]
                    year = str(now.year)
                    slot_dt = datetime.datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
                else:
                    slot_dt = datetime.datetime.fromisoformat(slot_date)
                if '-' in ts:
                    start_time = ts.split('-')[0].strip()
                    slot_dt = slot_dt.replace(hour=int(start_time[:2]), minute=int(start_time[3:5]))
                # Можно записаться, если слот сегодня и до него больше 4 часов, либо слот в будущем
                if slot_dt.date() > now.date() or (slot_dt.date() == now.date() and (slot_dt - now) >= datetime.timedelta(hours=4)):
                    time_slots.append(ts)
            except Exception:
                continue
        # Отправляем картинку перед выбором времени
        IMAGE_PATH = "zhim.png"  # имя файла картинки в корне проекта
        try:
            # Удаляем reply_markup у предыдущего сообщения, чтобы не было конфликтов
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        try:
            with open(IMAGE_PATH, "rb") as photo:
                await callback.message.answer_photo(photo)
        except Exception:
            pass
        if not time_slots:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_dates")]
                ]
            )
            # Вместо edit_text используем answer, чтобы всегда работала кнопка назад
            await callback.message.answer("Нет доступных временных интервалов на эту дату.", reply_markup=kb)
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=ts, callback_data=f"reg_time:{date}:{ts}")] for ts in time_slots
            ] + [
                [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_dates")]
            ]
        )
        await callback.message.edit_text(f"Выберите время для {date}:", reply_markup=kb)
        await state.update_data(date=date)
        await state.set_state(InterviewFSM.choosing_time)




@dp.callback_query(InterviewFSM.choosing_time, F.data.startswith("reg_time:"))
async def register_interview_confirm(callback: CallbackQuery, state: FSMContext):
    _, date, time_slot = callback.data.split(":", 2)
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        faculty_id = user.faculty_id
        # Проверяем, не записан ли уже кандидат на этот слот
        exists = await session.scalar(
            select(InterviewRegistration).where(
                InterviewRegistration.user_id == user.id,
                InterviewRegistration.date == date,
                InterviewRegistration.time_slot == time_slot,
                InterviewRegistration.canceled == False
            )
        )
        if exists:
            await callback.message.edit_text("Вы уже записаны на этот слот.")
            return
        # Получаем лимит
        slot_limit = await session.scalar(
            select(SlotLimit).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.date == date,
                SlotLimit.time_slot == time_slot
            )
        )
        if not slot_limit or slot_limit.limit <= 0:
            await callback.message.edit_text("Лимит на этот слот исчерпан.")
            return
        # Записываем кандидата
        reg = InterviewRegistration(
            user_id=user.id,
            faculty_id=faculty_id,
            date=date,
            time_slot=time_slot
        )
        session.add(reg)
        # Уменьшаем лимит
        slot_limit.limit -= 1
        await session.commit()

        # Получаем время блокировки для предупреждения
        hours_delta = await get_faculty_time_delta(session, faculty_id)

        # Сообщаем пользователю сразу, не дожидаясь Google Sheets
        text = (
            f"<b>Вы успешно записаны на собеседование!</b>\n"
            f"\n<b>Дата:</b> {date}"
            f"\n<b>Время:</b> {time_slot}\n"
            f"\n⚠️ <b>Важно:</b> Отменить запись можно только за {hours_delta} часов до начала собеседования!"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Отменить запись", callback_data="cancel_interview")]
            ]
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await state.clear()

        # --- Асинхронная задача для Google Sheet ---
        import asyncio
        import logging
        async def add_to_google_sheet(user_id, first_name, last_name, faculty_id, date, time_slot):
            try:
                import gspread
                from gspread_formatting import DataValidationRule, BooleanCondition, set_data_validation_for_cell_range
                gc = gspread.service_account(filename="credentials.json")
                # Получаем факультет
                async for session2 in get_session():
                    faculty = await session2.scalar(select(Faculty).where(Faculty.id == faculty_id))
                    if not (faculty and faculty.google_sheet_url):
                        logging.error(f"[GSHEET] Не найден факультет или ссылка на таблицу: faculty_id={faculty_id}")
                        return
                    sh = gc.open_by_url(faculty.google_sheet_url)
                    try:
                        ws = sh.worksheet("Записи")
                    except Exception as e:
                        logging.warning(f"[GSHEET] Не найден лист 'Записи', создаём: {e}")
                        ws = sh.add_worksheet(title="Записи", rows="100", cols="10")
                    # Получаем всех собесеров факультета
                    result_all_sobesers = await session2.execute(
                        select(User).where(User.is_sobeser == True, User.faculty_id == faculty_id)
                    )
                    all_sobesers = result_all_sobesers.scalars().all()
                    all_sobesers_names = [f"{s.first_name} {s.last_name}" for s in all_sobesers]
                    # Получаем собесеров, которые могут в это время
                    result_avail = await session2.execute(
                        select(User).join(Availability, Availability.user_id == User.id).where(
                            User.is_sobeser == True,
                            User.faculty_id == faculty_id,
                            Availability.date == date,
                            Availability.time_slot == time_slot,
                            Availability.is_available == True
                        )
                    )
                    avail_sobesers = result_avail.scalars().all()
                    avail_names = [f"{s.first_name} {s.last_name}" for s in avail_sobesers]
                    # Удаляем старую запись, если есть (по id кандидата)
                    all_rows = ws.get_all_values()
                    id_str = str(user_id)
                    to_delete = []
                    for idx, row in enumerate(all_rows, 1):
                        if row and row[0] == id_str:
                            to_delete.append(idx)
                    for idx in reversed(to_delete):
                        ws.delete_rows(idx)
                        logging.info(f"[GSHEET] Удалена старая запись: row={idx}, user_id={user_id}")
                        await asyncio.sleep(5)
                    # Добавляем новую строку с пустыми значениями для dropdown
                    row = [
                        str(user_id),
                        f"{first_name} {last_name}",
                        "", "", "", ""
                    ]
                    ws.append_row(row)
                    logging.info(f"[GSHEET] Добавлена строка: {row}")
                    await asyncio.sleep(5)
                    row_num = len(all_rows) - len(to_delete) + 1
                    # Добавляем dropdown для 3 и 4 столбца (только те, кто может)
                    if avail_names:
                        rule_avail = DataValidationRule(
                            BooleanCondition('ONE_OF_LIST', avail_names),
                            showCustomUi=True
                        )
                        set_data_validation_for_cell_range(ws, f"C{row_num}:D{row_num}", rule_avail)
                        logging.info(f"[GSHEET] Добавлен dropdown для C{row_num}:D{row_num} — {avail_names}")
                        await asyncio.sleep(5)
                    # Для 5 и 6 столбца — все собесеры факультета
                    if all_sobesers_names:
                        rule_all = DataValidationRule(
                            BooleanCondition('ONE_OF_LIST', all_sobesers_names),
                            showCustomUi=True
                        )
                        set_data_validation_for_cell_range(ws, f"E{row_num}:F{row_num}", rule_all)
                        logging.info(f"[GSHEET] Добавлен dropdown для E{row_num}:F{row_num} — {all_sobesers_names}")
                        await asyncio.sleep(5)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                logging.error(f"[GSHEET] Ошибка при добавлении записи: {e}\n{tb}")
        # Запускаем задачу в фоне
        asyncio.create_task(add_to_google_sheet(user.id, user.first_name, user.last_name, faculty_id, date, time_slot))
        # Уведомляем админа факультета с указанием собеседующих
        admin = await session.scalar(select(User).where(User.id == (await session.scalar(select(Faculty.admin_id).where(Faculty.id == faculty_id)))))
        if admin:
            try:
                # Получаем собеседующих, которые могут в это время
                result_sobesers = await session.execute(
                    select(User).join(Availability, Availability.user_id == User.id).where(
                        User.is_sobeser == True,
                        User.faculty_id == faculty_id,
                        Availability.date == date,
                        Availability.time_slot == time_slot,
                        Availability.is_available == True
                    )
                )
                sobesers = result_sobesers.scalars().all()
                sobesers_list = '\n'.join([f"• {s.first_name} {s.last_name}" for s in sobesers]) or "Нет доступных собеседующих"
                msg = (
                    f"Кандидат <b>{user.first_name} {user.last_name}</b> записался на собеседование:\n"
                    f"<b>Дата:</b> {date}\n<b>Время:</b> {time_slot}\n\n"
                    f"<b>Доступные собеседующие:</b>\n{sobesers_list}"
                )
                await bot.send_message(admin.tg_id, msg, parse_mode="HTML")
            except Exception:
                pass
        # ...отбивка теперь выше, чтобы не ждать Google Sheets


@dp.callback_query(InterviewFSM.choosing_time, F.data == "reg_back_to_dates")
async def register_interview_back_to_dates(callback: CallbackQuery, state: FSMContext):
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        faculty_id = user.faculty_id
        now = datetime.datetime.now()
        result = await session.execute(
            select(SlotLimit.date).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.limit > 0
            ).distinct()
        )
        # Фильтруем даты по актуальности (как в выборе даты)
        dates = []
        for r in result.all():
            try:
                slot_date = r[0]
                if len(slot_date) >= 5 and slot_date[2] == '.':
                    day, month = slot_date[:2], slot_date[3:5]
                    year = str(now.year)
                    slot_dt = datetime.datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
                else:
                    slot_dt = datetime.datetime.fromisoformat(slot_date)
                if slot_dt.date() > now.date() or (slot_dt.date() == now.date() and (slot_dt - now) >= datetime.timedelta(hours=4)):
                    dates.append(slot_date)
            except Exception:
                continue
        if not dates:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_menu")]
                ]
            )
            await callback.message.answer("Нет доступных дат для записи.", reply_markup=kb)
            await state.set_state(InterviewFSM.choosing_date)
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=date, callback_data=f"reg_date:{date}")] for date in dates
            ] + [
                [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_menu")]
            ]
        )
        await callback.message.edit_text("Выберите дату для собеседования:", reply_markup=kb)
        await state.set_state(InterviewFSM.choosing_date)


# --- Обработчик кнопки 'Назад' на этапе подтверждения (возврат к выбору времени) ---
@dp.callback_query(F.data.startswith("reg_back_to_times:"))
async def register_interview_back_to_times(callback: CallbackQuery, state: FSMContext):
    date = callback.data.split(":", 1)[1]
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        faculty_id = user.faculty_id
        result = await session.execute(
            select(SlotLimit.time_slot).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.date == date,
                SlotLimit.limit > 0
            )
        )
        time_slots = []
        now = datetime.datetime.now()
        for r in result.all():
            try:
                slot_time = r[0]
                # date уже есть в переменной date
                if len(date) >= 5 and date[2] == '.':
                    day, month = date[:2], date[3:5]
                    year = str(now.year)
                    slot_dt = datetime.datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
                else:
                    slot_dt = datetime.datetime.fromisoformat(date)
                if '-' in slot_time:
                    start_time = slot_time.split('-')[0].strip()
                    slot_dt = slot_dt.replace(hour=int(start_time[:2]), minute=int(start_time[3:5]))
                if slot_dt.date() > now.date() or (slot_dt.date() == now.date() and (slot_dt - now) >= datetime.timedelta(hours=4)):
                    time_slots.append(slot_time)
            except Exception:
                continue
        if not time_slots:
            await callback.message.edit_text("Нет доступных временных интервалов на эту дату.")
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=ts, callback_data=f"reg_time:{date}:{ts}")] for ts in time_slots
            ] + [
                [InlineKeyboardButton(text="Назад", callback_data="reg_back_to_dates")]
            ]
        )
        await callback.message.edit_text(f"Выберите время для {date}:", reply_markup=kb)
        await state.update_data(date=date)
        await state.set_state(InterviewFSM.choosing_time)



# --- Отмена записи: проверка времени и система причин ---
@dp.callback_query(F.data == "cancel_interview")
async def cancel_interview_callback(callback: CallbackQuery, state: FSMContext):
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        reg = await session.scalar(
            select(InterviewRegistration).where(
                InterviewRegistration.user_id == user.id,
                InterviewRegistration.canceled == False
            )
        )
        if not reg:
            await callback.message.edit_text("У вас нет активной записи.")
            return
        
        # Проверяем время до собеседования
        now = datetime.datetime.now()
        try:
            if len(reg.date) >= 5 and reg.date[2] == '.':
                day, month = reg.date[:2], reg.date[3:5]
                year = str(now.year)
                interview_dt = datetime.datetime.strptime(f"{day}.{month}.{year}", "%d.%m.%Y")
            else:
                interview_dt = datetime.datetime.fromisoformat(reg.date)
            
            # Парсим время начала собеседования
            if '-' in reg.time_slot:
                start_time = reg.time_slot.split('-')[0].strip()
                interview_dt = interview_dt.replace(hour=int(start_time[:2]), minute=int(start_time[3:5]))
            
            # Получаем время блокировки для факультета
            hours_delta = await get_faculty_time_delta(session, reg.faculty_id)
            
            # Проверяем, можно ли отменить
            time_until_interview = interview_dt - now
            if time_until_interview <= datetime.timedelta(hours=hours_delta):
                await callback.message.edit_text(
                    f"❌ <b>Отмена невозможна!</b>\n\n"
                    f"До собеседования осталось менее {hours_delta} часов.\n"
                    f"Отменить запись можно только за {hours_delta} часов до начала.",
                    parse_mode="HTML"
                )
                return
            
            # Если можно отменить, запрашиваем причину
            await callback.message.edit_text(
                f"📝 <b>Укажите причину отмены записи:</b>\n\n"
                f"<b>Дата:</b> {reg.date}\n"
                f"<b>Время:</b> {reg.time_slot}\n\n"
                f"Напишите причину отмены в следующем сообщении:",
                parse_mode="HTML"
            )
            await state.set_state(CancelFSM.waiting_reason)
            await state.update_data(registration_id=reg.id)
            
        except Exception as e:
            await callback.message.edit_text("Ошибка при обработке времени собеседования.")
            return

# --- Обработка причины отмены ---
@dp.message(CancelFSM.waiting_reason)
async def cancel_reason_handler(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    if not reason:
        await message.answer("Пожалуйста, укажите причину отмены:")
        return
    
    data = await state.get_data()
    registration_id = data.get('registration_id')
    
    async for session in get_session():
        # Получаем запись
        reg = await session.scalar(
            select(InterviewRegistration).where(InterviewRegistration.id == registration_id)
        )
        if not reg:
            await message.answer("Запись не найдена.")
            await state.clear()
            return
        
        # Получаем админа факультета
        admin = await session.scalar(
            select(User).where(User.id == (await session.scalar(
                select(Faculty.admin_id).where(Faculty.id == reg.faculty_id)
            )))
        )
        
        if not admin:
            await message.answer("Администратор факультета не найден.")
            await state.clear()
            return
        
        # Отправляем админу запрос на отмену
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разрешить отмену", callback_data=f"admin_approve_cancel:{registration_id}"),
                InlineKeyboardButton(text="❌ Запретить отмену", callback_data=f"admin_reject_cancel:{registration_id}")
            ]
        ])
        
        admin_message = (
            f"📋 <b>Запрос на отмену записи</b>\n\n"
            f"<b>Кандидат:</b> {message.from_user.first_name} {message.from_user.last_name}\n"
            f"<b>Дата:</b> {reg.date}\n"
            f"<b>Время:</b> {reg.time_slot}\n"
            f"<b>Причина:</b> {reason}\n\n"
            f"Выберите действие:"
        )
        
        try:
            await bot.send_message(admin.tg_id, admin_message, reply_markup=kb, parse_mode="HTML")
            await message.answer(
                "✅ <b>Запрос на отмену отправлен администратору!</b>\n\n"
                "Ожидайте решения. Вам придет уведомление о результате.",
                parse_mode="HTML"
            )
        except Exception as e:
            await message.answer("Ошибка при отправке запроса администратору.")
        
        await state.clear()

# --- Обработчики кнопок админа для отмены ---
@dp.callback_query(F.data.startswith("admin_approve_cancel:"))
async def admin_approve_cancel(callback: CallbackQuery):
    registration_id = int(callback.data.split(":")[-1])
    
    async for session in get_session():
        # Получаем запись
        reg = await session.scalar(
            select(InterviewRegistration).where(InterviewRegistration.id == registration_id)
        )
        if not reg:
            await callback.message.edit_text("Запись не найдена.")
            return
        
        # Получаем пользователя
        user = await session.scalar(select(User).where(User.id == reg.user_id))
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            return
        
        # Удаляем запись
        await session.delete(reg)
        
        # Возвращаем лимит
        slot_limit = await session.scalar(
            select(SlotLimit).where(
                SlotLimit.faculty_id == reg.faculty_id,
                SlotLimit.date == reg.date,
                SlotLimit.time_slot == reg.time_slot
            )
        )
        if slot_limit:
            slot_limit.limit += 1
        
        await session.commit()
        
        # Уведомляем кандидата
        try:
            await bot.send_message(
                user.tg_id,
                f"✅ <b>Ваша запись отменена!</b>\n\n"
                f"<b>Дата:</b> {reg.date}\n"
                f"<b>Время:</b> {reg.time_slot}\n\n"
                f"Теперь вы можете записаться на другое время.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        
        # Уведомляем админа
        await callback.message.edit_text(
            f"✅ <b>Отмена разрешена!</b>\n\n"
            f"Кандидат {user.first_name} {user.last_name} уведомлен об отмене записи.",
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("admin_reject_cancel:"))
async def admin_reject_cancel(callback: CallbackQuery):
    registration_id = int(callback.data.split(":")[-1])
    
    async for session in get_session():
        # Получаем запись
        reg = await session.scalar(
            select(InterviewRegistration).where(InterviewRegistration.id == registration_id)
        )
        if not reg:
            await callback.message.edit_text("Запись не найдена.")
            return
        
        # Получаем пользователя
        user = await session.scalar(select(User).where(User.id == reg.user_id))
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            return
        
        # Уведомляем кандидата
        try:
            await bot.send_message(
                user.tg_id,
                f"❌ <b>Отмена записи отклонена</b>\n\n"
                f"<b>Дата:</b> {reg.date}\n"
                f"<b>Время:</b> {reg.time_slot}\n\n"
                f"Ваша запись остается активной.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        
        # Уведомляем админа
        await callback.message.edit_text(
            f"❌ <b>Отмена запрещена!</b>\n\n"
            f"Кандидат {user.first_name} {user.last_name} уведомлен об отклонении отмены.",
            parse_mode="HTML"
        )

@dp.message(Command("role"))
async def get_role(message: Message):
	tg_id = str(message.from_user.id)
	async for session in get_session():
		result = await session.execute(select(User).where(User.tg_id == tg_id))
		user = result.scalar_one_or_none()
		if not user:
			await message.answer("Вы не найдены в базе данных.")
			return
		roles = []
		faculty_info = ""
		if user.is_admin_faculty:
			roles.append("Админ факультета")
			# Асинхронно получаем факультет, где этот пользователь админ
			result_faculty = await session.execute(
				select(Faculty).where(Faculty.admin_id == user.id)
			)
			faculty = result_faculty.scalar_one_or_none()
			if faculty:
				faculty_info = f"\nФакультет: <b>{faculty.name}</b>"
				if faculty.google_sheet_url:
					faculty_info += f"\nGoogle-таблица: {faculty.google_sheet_url}"
		if user.is_sobeser:
			roles.append("Собеседующий")
		if user.is_candidate:
			roles.append("Кандидат")
		if not roles:
			roles.append("Пользователь без роли")
		await message.answer(f"Ваша роль: {', '.join(roles)}{faculty_info}")

@dp.message(Command("set_people"))
async def set_people(message: Message):
    tg_id = str(message.from_user.id)
    await message.answer("Начинаю загрузку данных. Это может занять несколько минут...")
    try:
        async for session in get_session():
            # Получаем максимальный id в users
            max_id_result = await session.execute(select(func.max(User.id)))
            max_id = max_id_result.scalar() or 0
            next_id = max_id + 1
            # Проверяем, что пользователь — админ факультета
            result_user = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
            row = result_user.first()
            if not row:
                await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
                return
            user, faculty = row
            if not faculty.google_sheet_url:
                await message.answer("У факультета не указана ссылка на Google-таблицу.")
                return
            # Авторизация gspread
            gc = gspread.service_account(filename="credentials.json")
            sh = gc.open_by_url(faculty.google_sheet_url)
            # Парсим кандидатов
            ws_candidates = sh.worksheet("Кандидаты")
            candidates = ws_candidates.get_all_values()[1:]  # пропускаем заголовок
            added_candidates = 0
            for row in candidates:
                if not row or not row[0] or not row[1] or not row[2]:
                    break
                first_name, last_name, vk_id = row[0], row[1], row[2]
                stmt = insert(Candidate).values(
                    first_name=first_name,
                    last_name=last_name,
                    vk_id=vk_id,
                    faculty_id=faculty.id
                ).on_conflict_do_nothing(index_elements=[Candidate.vk_id])
                await session.execute(stmt)
                added_candidates += 1
            # Парсим опытных собесеров
            ws_exp = sh.worksheet("Опытные собесеры")
            exp_rows = ws_exp.get_all_values()[1:]
            added_exp = 0
            for row in exp_rows:
                if not row or not row[2] or not row[3]:
                    break
                first_name, last_name = row[2], row[3]
                stmt = insert(User).values(
                    first_name=first_name,
                    last_name=last_name,
                    is_sobeser=True,
                    faculty_id=faculty.id
                )
                await session.execute(stmt)
                added_exp += 1
            # Парсим не опытных собесеров
            ws_noexp = sh.worksheet("Не опытные собесеры")
            noexp_rows = ws_noexp.get_all_values()[1:]
            added_noexp = 0
            for row in noexp_rows:
                if not row or not row[2] or not row[3]:
                    break
                first_name, last_name = row[2], row[3]
                stmt = insert(User).values(
                    first_name=first_name,
                    last_name=last_name,
                    is_sobeser=True,
                    faculty_id=faculty.id
                )
                await session.execute(stmt)
                added_noexp += 1
            await session.commit()
            await message.answer(f"Добавлено кандидатов: {added_candidates}\nОпытных собесеров: {added_exp}\nНе опытных собесеров: {added_noexp}")
    except Exception as e:
        tb = traceback.format_exc()
        await message.answer(f"Произошла ошибка при загрузке данных:\n<pre>{e}\n{tb[-1500:]}</pre>")

@dp.message(Command("create_list"))
async def create_list(message: Message):
    tg_id = str(message.from_user.id)
    await message.answer("Создаю лист для отметки времени...")
    try:
        async for session in get_session():
            # Получаем пользователя и факультет
            result = await session.execute(select(User, Faculty).join(Faculty, Faculty.id == User.faculty_id).where(User.tg_id == tg_id))
            row = result.first()
            if not row:
                await message.answer("Вы не привязаны к факультету или не зарегистрированы.")
                return
            user, faculty = row
            if not faculty.google_sheet_url:
                await message.answer("У факультета не указана ссылка на Google-таблицу.")
                return
            gc = gspread.service_account(filename="credentials.json")
            sh = gc.open_by_url(faculty.google_sheet_url)
            # Имя листа
            sheet_name = f"{user.first_name}_{user.last_name}"
            if sheet_name in [ws.title for ws in sh.worksheets()]:
                await message.answer("Лист с таким именем уже существует!")
                return
            worksheet = sh.add_worksheet(title=sheet_name, rows="20", cols="10")
            # Заполняем даты по горизонтали (B1:H1)
            dates = ["26.09(пт)", "27.09(cб)", "28.09(вск)", "29.09(пн)", "30.09(вт)", "01.10(ср)", "02.10(чт)", "03.10(пт)"]
            worksheet.update([dates], "B1")
            # Заполняем интервалы по вертикали (A2:A13)
            times = [
                "10:00 - 11:00", "11:00 - 12:00", "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00", "19:00 - 20:00", "20:00 - 21:00", "21:00 - 22:00"
            ]
            for i, t in enumerate(times, start=2):
                worksheet.update([[t]], f"A{i}")
            # Добавляем dropdown в B2:H13
            rule = {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "могу"},
                        {"userEnteredValue": "не могу"}
                    ]
                },
                "showCustomUi": True,
                "strict": True
            }
            requests = []
            for row in range(2, 14):
                for col in range(2, 10):
                    requests.append({
                        "setDataValidation": {
                            "range": {
                                "sheetId": worksheet._properties["sheetId"],
                                "startRowIndex": row-1,
                                "endRowIndex": row,
                                "startColumnIndex": col-1,
                                "endColumnIndex": col
                            },
                            "rule": rule
                        }
                    })
            sh.batch_update({"requests": requests})
            worksheet.update([[str(user.id)]], "A15")
            await message.answer(f"Лист {sheet_name} успешно создан!")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        short_tb = tb[-500:] if len(tb) > 500 else tb
        await message.answer(f"Ошибка при создании листа:\n<pre>{e}\n{short_tb}</pre>")

@dp.message(Command("create_lists"))
async def create_lists(message: Message):
    tg_id = str(message.from_user.id)
    await message.answer("Создаю листы для всех собеседующих факультета...")
    try:
        async for session in get_session():
            # Получаем админа и факультет
            result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
            row = result.first()
            if not row:
                await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
                return
            admin, faculty = row
            if not faculty.google_sheet_url:
                await message.answer("У факультета не указана ссылка на Google-таблицу.")
                return
            gc = gspread.service_account(filename="credentials.json")
            sh = gc.open_by_url(faculty.google_sheet_url)
            # Получаем всех собеседующих этого факультета
            result_sobesers = await session.execute(select(User).where(User.is_sobeser == True, User.faculty_id == faculty.id))
            sobesers = result_sobesers.scalars().all()
            dates = ["26.09(пт)", "27.09(cб)", "28.09(вск)", "29.09(пн)", "30.09(вт)", "01.10(ср)", "02.10(чт)", "03.10(пт)"]
            times = [
                "10:00 - 11:00", "11:00 - 12:00", "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00", "19:00 - 20:00", "20:00 - 21:00", "21:00 - 22:00"
            ]
            rule = {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "могу"},
                        {"userEnteredValue": "не могу"}
                    ]
                },
                "showCustomUi": True,
                "strict": True
            }
            created = 0
            existing_sheets = {ws.title for ws in sh.worksheets()}
            for user in sobesers:
                sheet_name = f"{user.first_name}_{user.last_name}"
                if sheet_name in existing_sheets:
                    continue
                retry_count = 0
                while retry_count < 3:
                    try:
                        worksheet = sh.add_worksheet(title=sheet_name, rows="20", cols="10")
                        worksheet.update([dates], "B1")
                        for i, t in enumerate(times, start=2):
                            worksheet.update([[t]], f"A{i}")
                        requests = []
                        for row in range(2, 14):
                            for col in range(2, 10):
                                requests.append({
                                    "setDataValidation": {
                                        "range": {
                                            "sheetId": worksheet._properties["sheetId"],
                                            "startRowIndex": row-1,
                                            "endRowIndex": row,
                                            "startColumnIndex": col-1,
                                            "endColumnIndex": col
                                        },
                                        "rule": rule
                                    }
                                })
                        sh.batch_update({"requests": requests})
                        worksheet.update([[str(user.id)]], "A15")
                        created += 1
                        await asyncio.sleep(5)
                        break
                    except gspread.exceptions.APIError as e:
                        if "429" in str(e):
                            retry_count += 1
                            await asyncio.sleep(30 * retry_count)
                        else:
                            break
                    except Exception:
                        break
            await message.answer(f"Создано листов: {created}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        short_tb = tb[-500:] if len(tb) > 500 else tb
        await message.answer(f"Ошибка при создании листов:\n<pre>{e}\n{short_tb}</pre>")

@dp.message(Command("parse_availability"))
async def parse_availability(message: Message):
    tg_id = str(message.from_user.id)
    await message.answer("Начинаю парсинг доступности всех собеседующих...")
    try:
        async for session in get_session():
            # Получаем админа и факультет
            result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
            row = result.first()
            if not row:
                await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
                return
            admin, faculty = row
            if not faculty.google_sheet_url:
                await message.answer("У факультета не указана ссылка на Google-таблицу.")
                return
            # Удаляем старую занятость для факультета
            await session.execute(
                Availability.__table__.delete().where(Availability.faculty_id == faculty.id)
            )
            await session.commit()
            gc = gspread.service_account(filename="credentials.json")
            sh = gc.open_by_url(faculty.google_sheet_url)
            exclude = {"Кандидаты", "Опытные собесеры", "Не опытные собесеры"}
            sheets = [ws for ws in sh.worksheets() if ws.title not in exclude]
            added = 0
            for ws in sheets:
                try:
                    user_id_cell = ws.acell("A15").value
                    if not user_id_cell:
                        continue
                    user_id = int(user_id_cell)
                    # Даты в B1:I1 (8 столбцов)
                    date_cells = ws.range("B1:I1")
                    date_values = [cell.value for cell in date_cells]
                    # Временные интервалы в A2:A13 (12 строк)
                    time_cells = ws.range("A2:A13")
                    time_values = [cell.value for cell in time_cells]
                    # Парсим диапазон B2:I13 (12 строк x 8 столбцов)
                    grid = ws.range("B2:I13")
                    for i, cell in enumerate(grid):
                        row = i // 8  # 0..11
                        col = i % 8   # 0..7
                        value = cell.value.strip().lower()
                        if value == "могу":
                            date = date_values[col]
                            time_slot = time_values[row]
                            stmt = insert(Availability).values(
                                user_id=user_id,
                                faculty_id=faculty.id,
                                date=date,
                                time_slot=time_slot,
                                is_available=True
                            ).on_conflict_do_nothing()
                            await session.execute(stmt)
                            added += 1
                except Exception:
                    continue
            await session.commit()
            await message.answer(f"Добавлено доступных слотов: {added}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        short_tb = tb[-500:] if len(tb) > 500 else tb
        await message.answer(f"Ошибка при парсинге:<pre>{e}\n{short_tb}</pre>")

@dp.message(Command("create_slots"))
async def create_slots(message: Message):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Получаем все даты, где есть хотя бы один 'могу'
        result_dates = await session.execute(
            select(Availability.date).where(
                Availability.faculty_id == faculty.id,
                Availability.is_available == True
            ).distinct()
        )
        dates = [r[0] for r in result_dates.all()]
        # Получаем лимиты слотов для каждой даты (по умолчанию 0)
        result_limits = await session.execute(
            select(SlotLimit.date, func.sum(SlotLimit.limit)).where(
                SlotLimit.faculty_id == faculty.id
            ).group_by(SlotLimit.date)
        )
        slot_limits = dict(result_limits.all())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=f"{date}", callback_data=f"slot_date:{date}")] for date in dates],
            [InlineKeyboardButton(text="Назад", callback_data="slot_back")]
        ])
        text = "<b>Выберите дату для создания слотов.</b>\n\n"
        text += "Даты и количество сделанных слотов (по умолчанию 0):\n"
        for date in dates:
            limit = slot_limits.get(date, 0)
            text += f"• {date} — <b>{limit}</b> слотов\n"
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("slot_date:"))
async def slot_date_callback(callback: CallbackQuery):
    date = callback.data.split(":", 1)[1]
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Получаем все интервалы времени, где есть хотя бы один 'могу'
        result_times = await session.execute(
            select(Availability.time_slot).where(
                Availability.faculty_id == faculty.id,
                Availability.date == date,
                Availability.is_available == True
            ).distinct()
        )
        time_slots = [r[0] for r in result_times.all()]
        # Получаем лимиты слотов для каждого времени (по умолчанию 0)
        result_limits = await session.execute(
            select(SlotLimit.time_slot, SlotLimit.limit).where(
                SlotLimit.faculty_id == faculty.id,
                SlotLimit.date == date
            )
        )
        slot_limits = dict(result_limits.all())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=f"{time_slot}", callback_data=f"slot_time:{date}:{time_slot}")] for time_slot in time_slots],
            [InlineKeyboardButton(text="Назад", callback_data="create_slots")]
        ])
        text = f"<b>Выберите время для даты {date}.</b>\n\n"
        text += "Временные интервалы и количество сделанных слотов (по умолчанию 0):\n"
        for time_slot in time_slots:
            limit = slot_limits.get(time_slot, 0)
            text += f"• {time_slot} — <b>{limit}</b> слотов\n"
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "create_slots")
async def back_to_dates(callback: CallbackQuery):
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Получаем все даты, где есть хотя бы один 'могу'
        result_dates = await session.execute(
            select(Availability.date).where(
                Availability.faculty_id == faculty.id,
                Availability.is_available == True
            ).distinct()
        )
        dates = [r[0] for r in result_dates.all()]
        # Получаем лимиты слотов для каждой даты (по умолчанию 0)
        result_limits = await session.execute(
            select(SlotLimit.date, func.sum(SlotLimit.limit)).where(
                SlotLimit.faculty_id == faculty.id
            ).group_by(SlotLimit.date)
        )
        slot_limits = dict(result_limits.all())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=f"{date}", callback_data=f"slot_date:{date}")] for date in dates],
            [InlineKeyboardButton(text="Назад", callback_data="slot_back")]
        ])
        text = "<b>Выберите дату для создания слотов.</b>\n\n"
        text += "Даты и количество сделанных слотов (по умолчанию 0):\n"
        for date in dates:
            limit = slot_limits.get(date, 0)
            text += f"• {date} — <b>{limit}</b> слотов\n"
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("slot_time:"))
async def slot_time_callback(callback: CallbackQuery):
    _, date, time_slot = callback.data.split(":", 2)
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Получаем всех пользователей, которые могут в это время и день
        result_users = await session.execute(
            select(User).join(Availability, Availability.user_id == User.id).where(
                Availability.faculty_id == faculty.id,
                Availability.date == date,
                Availability.time_slot == time_slot,
                Availability.is_available == True
            )
        )
        users = result_users.scalars().all()
        user_list = "\n".join([f"• {u.first_name} {u.last_name}" for u in users]) or "Нет доступных людей"
        result_limit = await session.execute(
            select(SlotLimit.limit).where(
                SlotLimit.faculty_id == faculty.id,
                SlotLimit.date == date,
                SlotLimit.time_slot == time_slot
            )
        )
        slot_limit = result_limit.scalar()
        current_slots = slot_limit if slot_limit is not None else 0
        kb = InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=str(i), callback_data=f"slot_count:{date}:{time_slot}:{i}")] for i in range(0, 11)],
            [
                InlineKeyboardButton(text="Добавить", callback_data=f"slot_add:{date}:{time_slot}"),
                InlineKeyboardButton(text="Удалить", callback_data=f"slot_del:{date}:{time_slot}")
            ],
            [InlineKeyboardButton(text="Назад", callback_data=f"slot_date:{date}")]
        ])
        text = (
            f"<b>{date} — {time_slot}</b>\n\n"
            f"<b>Доступные люди:</b>\n{user_list}\n\n"
            f"<b>Текущее количество слотов:</b> <b>{current_slots}</b>\n\n"
            f"Выберите лимит, добавьте или удалите места."
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

# --- Добавление слотов: обработка кнопки 'Добавить' ---
@dp.callback_query(F.data.startswith("slot_add:"))
async def slot_add_callback(callback: CallbackQuery):
    _, date, time_slot = callback.data.split(":", 2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=str(i), callback_data=f"slot_add_count:{date}:{time_slot}|{i}")] for i in range(0, 11)],
        [InlineKeyboardButton(text="Назад", callback_data=f"slot_add_back:{date}:{time_slot}")]
    ])
    await callback.message.edit_text(f"Сколько мест добавить к {date} {time_slot}?", reply_markup=kb)

# Кнопка назад из режима добавления
@dp.callback_query(F.data.startswith("slot_add_back:"))
async def slot_add_back_callback(callback: CallbackQuery):
    _, date, time_slot = callback.data.split(":", 2)
    await slot_time_callback(callback=callback)

# --- Обработка выбора количества для добавления ---
@dp.callback_query(F.data.startswith("slot_add_count:"))
async def slot_add_count_callback(callback: CallbackQuery):
    # slot_add_count:date:time_slot|add_count
    data = callback.data[len("slot_add_count:"):]
    slot_info, add_count = data.split("|", 1)
    date, time_slot = slot_info.split(":", 1)
    try:
        add_count = int(add_count)
    except Exception:
        await callback.message.edit_text("Ошибка: не удалось определить количество для добавления.")
        return
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        slot_limit_obj = await session.scalar(
            select(SlotLimit).where(
                SlotLimit.faculty_id == faculty.id,
                SlotLimit.date == date,
                SlotLimit.time_slot == time_slot
            )
        )
        before = slot_limit_obj.limit if slot_limit_obj else 0
        if slot_limit_obj:
            slot_limit_obj.limit += add_count
        else:
            slot_limit_obj = SlotLimit(
                faculty_id=faculty.id,
                date=date,
                time_slot=time_slot,
                limit=add_count
            )
            session.add(slot_limit_obj)
        await session.commit()
        after = slot_limit_obj.limit
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data=f"slot_time:{date}:{time_slot}")]
        ])
        await callback.message.edit_text(
            f"Добавлено <b>{add_count}</b> мест к {date} {time_slot}.\n\nБыло: <b>{before}</b>\nСтало: <b>{after}</b>",
            reply_markup=kb, parse_mode="HTML"
        )

# --- Удаление слотов: обработка кнопки 'Удалить' ---
@dp.callback_query(F.data.startswith("slot_del:"))
async def slot_del_callback(callback: CallbackQuery):
    _, date, time_slot = callback.data.split(":", 2)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=str(i), callback_data=f"slot_del_count:{date}:{time_slot}|{i}")] for i in range(0, 11)],
        [InlineKeyboardButton(text="Назад", callback_data=f"slot_del_back:{date}:{time_slot}")]
    ])
    await callback.message.edit_text(f"Сколько мест удалить из {date} {time_slot}?", reply_markup=kb)

# Кнопка назад из режима удаления
@dp.callback_query(F.data.startswith("slot_del_back:"))
async def slot_del_back_callback(callback: CallbackQuery):
    _, date, time_slot = callback.data.split(":", 2)
    await slot_time_callback(callback=callback)

# --- Обработка выбора количества для удаления ---
@dp.callback_query(F.data.startswith("slot_del_count:"))
async def slot_del_count_callback(callback: CallbackQuery):
    # slot_del_count:date:time_slot|del_count
    data = callback.data[len("slot_del_count:"):]
    slot_info, del_count = data.split("|", 1)
    date, time_slot = slot_info.split(":", 1)
    try:
        del_count = int(del_count)
    except Exception:
        await callback.message.edit_text("Ошибка: не удалось определить количество для удаления.")
        return
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        slot_limit_obj = await session.scalar(
            select(SlotLimit).where(
                SlotLimit.faculty_id == faculty.id,
                SlotLimit.date == date,
                SlotLimit.time_slot == time_slot
            )
        )
        before = slot_limit_obj.limit if slot_limit_obj else 0
        if slot_limit_obj and del_count > 0:
            slot_limit_obj.limit = max(0, slot_limit_obj.limit - del_count)
            await session.commit()
            after = slot_limit_obj.limit
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"slot_time:{date}:{time_slot}")]
            ])
            await callback.message.edit_text(
                f"Удалено <b>{del_count}</b> мест из {date} {time_slot}.\n\nБыло: <b>{before}</b>\nСтало: <b>{after}</b>",
                reply_markup=kb, parse_mode="HTML"
            )
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"slot_time:{date}:{time_slot}")]
            ])
            await callback.message.edit_text(
                f"Невозможно удалить {del_count} мест. Текущее количество: <b>{before}</b>.",
                reply_markup=kb, parse_mode="HTML"
            )

@dp.callback_query(F.data.startswith("slot_count:"))
async def slot_count_callback(callback: CallbackQuery):
    data = callback.data[len("slot_count:"):]
    rest, count = data.rsplit(":", 1)
    date, time_slot = rest.split(":", 1)
    count = int(count)
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Сохраняем/обновляем лимит слотов
        stmt = insert(SlotLimit).values(
            faculty_id=faculty.id,
            date=date,
            time_slot=time_slot,
            limit=count
        ).on_conflict_do_update(
            index_elements=[SlotLimit.faculty_id, SlotLimit.date, SlotLimit.time_slot],
            set_={"limit": count}
        )
        await session.execute(stmt)
        await session.commit()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data=f"slot_time:{date}:{time_slot}")]
        ])
        await callback.message.edit_text(f"Лимит слотов на {date} {time_slot} установлен: {count}", reply_markup=kb)


# --- Команда для админа: получить все записи кандидатов по дням ---
@dp.message(Command("get_zapis"))
async def get_zapis(message: types.Message):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        admin, faculty = row
        # Получаем все записи по факультету
        result_regs = await session.execute(
            select(InterviewRegistration, User)
            .join(User, User.id == InterviewRegistration.user_id)
            .where(InterviewRegistration.faculty_id == faculty.id, InterviewRegistration.canceled == False)
            .order_by(InterviewRegistration.date, InterviewRegistration.time_slot)
        )
        rows = result_regs.all()
        if not rows:
            await message.answer("Нет записей на собеседования.")
            return
        # Группируем по дате
        from collections import defaultdict
        zapis_by_date = defaultdict(list)
        for reg, user in rows:
            zapis_by_date[reg.date].append((reg.time_slot, user.first_name, user.last_name))
        # Формируем сообщения по дням
        for date, zapis in sorted(zapis_by_date.items()):
            zapis.sort()
            text = f"<b>{date}</b>\n"
            for time_slot, first_name, last_name in zapis:
                text += f"<b>{time_slot}</b>: {first_name} {last_name}\n"
            await message.answer(text, parse_mode="HTML")


# --- Глобальная диагностика всех факультетов ---
@dp.message(Command("get_fucking_stats"))
async def get_fucking_stats(message: types.Message):
    tg_id = str(message.from_user.id)
    
    # Проверяем доступ - только для конкретного пользователя
    if tg_id != "922109605":
        await message.answer("У вас нет доступа к этой команде.")
        return
    
    await message.answer("🔍 Начинаю глобальную диагностику всех факультетов...")
    
    try:
        async for session in get_session():
            # Получаем все факультеты
            result_faculties = await session.execute(select(Faculty))
            faculties = result_faculties.scalars().all()
            
            if not faculties:
                await message.answer("❌ Факультеты не найдены в базе данных.")
                return
            
            total_stats = {
                'faculties_checked': 0,
                'total_sheets_checked': 0,
                'total_slots_found': 0,
                'total_slots_in_db': 0,
                'total_missing_slots': 0,
                'faculty_details': []
            }
            
            # Исключаемые листы
            excluded_sheets = {"Кандидаты", "Опытные собесеры", "Не опытные собесеры", "Записи"}
            
            for faculty in faculties:
                if not faculty.google_sheet_url:
                    continue
                    
                faculty_stats = {
                    'faculty_name': faculty.name,
                    'faculty_id': faculty.id,
                    'sheets_checked': 0,
                    'slots_found': 0,
                    'slots_in_db': 0,
                    'missing_slots': 0,
                    'error': None
                }
                
                try:
                    # Подключаемся к Google Sheets
                    gc = gspread.service_account(filename="credentials.json")
                    sh = gc.open_by_url(faculty.google_sheet_url)
                    
                    # Получаем все листы
                    worksheets = sh.worksheets()
                    faculty_stats['total_sheets'] = len(worksheets)
                    
                    # Фильтруем листы (исключаем служебные)
                    user_sheets = [ws for ws in worksheets if ws.title not in excluded_sheets]
                    faculty_stats['sheets_checked'] = len(user_sheets)
                    
                    # Считаем слоты в Google Sheets
                    sheets_slots = 0
                    for ws in user_sheets:
                        try:
                            # Парсим лист доступности
                            user_id_cell = ws.acell("A15").value
                            if not user_id_cell:
                                continue
                                
                            # Даты в A1:I1 (включая A1)
                            date_cells = ws.range("A1:I1")
                            date_values = [cell.value for cell in date_cells if cell.value]
                            
                            # Временные интервалы в A2:A13 (12 строк)
                            time_cells = ws.range("A2:A13")
                            time_values = [cell.value for cell in time_cells if cell.value]
                            
                            # Парсим диапазон A2:I13 (9 столбцов x 12 строк)
                            grid = ws.range("A2:I13")
                            for i, cell in enumerate(grid):
                                row = i // 9  # 0..11 (12 строк)
                                col = i % 9   # 0..8 (9 столбцов)
                                
                                if row < len(time_values) and col < len(date_values):
                                    value = cell.value.strip().lower() if cell.value else ""
                                    if value == "могу":
                                        sheets_slots += 1
                            
                            # Задержка между запросами для избежания rate limiting
                            await asyncio.sleep(2.0)
                            
                        except Exception as e:
                            continue
                    
                    faculty_stats['slots_found'] = sheets_slots
                    
                    # Считаем слоты в базе данных для этого факультета
                    result_db_slots = await session.execute(
                        select(func.count(Availability.id)).where(
                            Availability.faculty_id == faculty.id,
                            Availability.is_available == True
                        )
                    )
                    db_slots = result_db_slots.scalar() or 0
                    faculty_stats['slots_in_db'] = db_slots
                    
                    # Вычисляем потерянные слоты
                    missing_slots = max(0, sheets_slots - db_slots)
                    faculty_stats['missing_slots'] = missing_slots
                    
                    # Обновляем общую статистику
                    total_stats['faculties_checked'] += 1
                    total_stats['total_sheets_checked'] += faculty_stats['sheets_checked']
                    total_stats['total_slots_found'] += sheets_slots
                    total_stats['total_slots_in_db'] += db_slots
                    total_stats['total_missing_slots'] += missing_slots
                    
                except Exception as e:
                    faculty_stats['error'] = str(e)
                
                total_stats['faculty_details'].append(faculty_stats)
                
                # Задержка между факультетами
                await asyncio.sleep(5)
            
            # Формируем отчет
            report = "📊 <b>ГЛОБАЛЬНАЯ ДИАГНОСТИКА ФАКУЛЬТЕТОВ</b>\n\n"
            
            # Общая статистика
            report += f"🏛️ <b>Факультетов проверено:</b> {total_stats['faculties_checked']}\n"
            report += f"📋 <b>Листов проверено:</b> {total_stats['total_sheets_checked']}\n"
            report += f"✅ <b>Слотов найдено в Google Sheets:</b> {total_stats['total_slots_found']}\n"
            report += f"💾 <b>Слотов в базе данных:</b> {total_stats['total_slots_in_db']}\n"
            report += f"❌ <b>Потерянных слотов:</b> {total_stats['total_missing_slots']}\n\n"
            
            # Детали по факультетам
            report += "<b>📋 ДЕТАЛИ ПО ФАКУЛЬТЕТАМ:</b>\n"
            for detail in total_stats['faculty_details']:
                if detail['error']:
                    report += f"\n❌ <b>{detail['faculty_name']}</b> - ОШИБКА: {detail['error']}"
                else:
                    report += f"\n🏛️ <b>{detail['faculty_name']}</b>\n"
                    report += f"   📋 Листов: {detail['sheets_checked']}\n"
                    report += f"   ✅ В Google Sheets: {detail['slots_found']}\n"
                    report += f"   💾 В БД: {detail['slots_in_db']}\n"
                    report += f"   ❌ Потеряно: {detail['missing_slots']}"
            
            # Отправляем отчет
            if len(report) > 4000:
                # Разбиваем на части если сообщение слишком длинное
                parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                for part in parts:
                    await message.answer(part, parse_mode="HTML")
            else:
                await message.answer(report, parse_mode="HTML")
                
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        await message.answer(f"❌ Ошибка при диагностике:\n<pre>{e}\n{tb[-1000:]}</pre>")


# --- Команда восстановления потерянных данных ---
@dp.message(Command("recover_missing_data"))
async def recover_missing_data(message: types.Message):
    tg_id = str(message.from_user.id)
    
    # Проверяем доступ - только для конкретного пользователя
    if tg_id != "922109605":
        await message.answer("У вас нет доступа к этой команде.")
        return
    
    await message.answer("🔄 Начинаю восстановление потерянных данных...")
    
    try:
        async for session in get_session():
            # Получаем все факультеты
            result_faculties = await session.execute(select(Faculty))
            faculties = result_faculties.scalars().all()
            
            recovered_count = 0
            errors = []
            
            # Исключаемые листы
            excluded_sheets = {"Кандидаты", "Опытные собесеры", "Не опытные собесеры", "Записи"}
            
            for faculty in faculties:
                if not faculty.google_sheet_url:
                    continue
                    
                try:
                    # Подключаемся к Google Sheets
                    gc = gspread.service_account(filename="credentials.json")
                    sh = gc.open_by_url(faculty.google_sheet_url)
                    
                    # Получаем все листы
                    worksheets = sh.worksheets()
                    user_sheets = [ws for ws in worksheets if ws.title not in excluded_sheets]
                    
                    for ws in user_sheets:
                        try:
                            # Парсим лист доступности
                            user_id_cell = ws.acell("A15").value
                            if not user_id_cell:
                                continue
                                
                            user_id = int(user_id_cell)
                            
                            # Даты в A1:I1 (включая A1)
                            date_cells = ws.range("A1:I1")
                            date_values = [cell.value for cell in date_cells if cell.value]
                            
                            # Временные интервалы в A2:A13 (12 строк)
                            time_cells = ws.range("A2:A13")
                            time_values = [cell.value for cell in time_cells if cell.value]
                            
                            # Парсим диапазон A2:I13 (9 столбцов x 12 строк)
                            grid = ws.range("A2:I13")
                            for i, cell in enumerate(grid):
                                row = i // 9  # 0..11 (12 строк)
                                col = i % 9   # 0..8 (9 столбцов)
                                
                                if row < len(time_values) and col < len(date_values):
                                    value = cell.value.strip().lower() if cell.value else ""
                                    if value == "могу":
                                        date = date_values[col]
                                        time_slot = time_values[row]
                                        
                                        # Проверяем, есть ли уже такая запись в БД
                                        existing = await session.scalar(
                                            select(Availability).where(
                                                Availability.user_id == user_id,
                                                Availability.faculty_id == faculty.id,
                                                Availability.date == date,
                                                Availability.time_slot == time_slot
                                            )
                                        )
                                        
                                        if not existing:
                                            # Добавляем недостающую запись
                                            new_availability = Availability(
                                                user_id=user_id,
                                                faculty_id=faculty.id,
                                                date=date,
                                                time_slot=time_slot,
                                                is_available=True
                                            )
                                            session.add(new_availability)
                                            recovered_count += 1
                            
                            # Задержка между запросами
                            await asyncio.sleep(2.0)
                            
                        except Exception as e:
                            continue
                    
                    # Задержка между факультетами
                    await asyncio.sleep(5)
                    
                except Exception as e:
                    errors.append(f"{faculty.name}: {str(e)}")
            
            # Сохраняем изменения
            await session.commit()
            
            # Отправляем результат
            result_msg = f"✅ <b>Восстановление завершено!</b>\n\n"
            result_msg += f"🔄 <b>Восстановлено записей:</b> {recovered_count}\n"
            
            if errors:
                result_msg += f"\n❌ <b>Ошибки:</b>\n"
                for error in errors[:5]:  # Показываем только первые 5 ошибок
                    result_msg += f"• {error}\n"
                if len(errors) > 5:
                    result_msg += f"... и еще {len(errors) - 5} ошибок"
            
            await message.answer(result_msg, parse_mode="HTML")
                
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        await message.answer(f"❌ Ошибка при восстановлении:\n<pre>{e}\n{tb[-1000:]}</pre>")

# --- Команда настройки времени блокировки слотов ---
@dp.message(Command("create_time_delta"))
async def create_time_delta(message: types.Message):
    tg_id = str(message.from_user.id)
    
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await message.answer("Вы не являетесь админом факультета или не привязаны к факультету.")
            return
        
        admin, faculty = row
        
        # Получаем текущее значение времени блокировки
        current_delta = await get_faculty_time_delta(session, faculty.id)
        
        # Создаем клавиатуру с вариантами времени
        kb = InlineKeyboardMarkup(inline_keyboard=[
            *[[InlineKeyboardButton(text=f"{i} часов", callback_data=f"set_delta:{i}")] for i in range(1, 25)],
            [InlineKeyboardButton(text="Назад", callback_data="delta_back")]
        ])
        
        await message.answer(
            f"⏰ <b>Настройка времени блокировки слотов</b>\n\n"
            f"<b>Факультет:</b> {faculty.name}\n"
            f"<b>Текущее значение:</b> {current_delta} часов\n\n"
            f"Выберите новое время блокировки (за сколько часов до собеседования нельзя записаться/отменить):",
            reply_markup=kb,
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("set_delta:"))
async def set_time_delta(callback: CallbackQuery):
    hours = int(callback.data.split(":")[-1])
    tg_id = str(callback.from_user.id)
    
    async for session in get_session():
        # Проверяем, что пользователь — админ факультета
        result = await session.execute(select(User, Faculty).join(Faculty, Faculty.admin_id == User.id).where(User.tg_id == tg_id))
        row = result.first()
        if not row:
            await callback.message.edit_text("Вы не являетесь админом факультета.")
            return
        
        admin, faculty = row
        
        # Обновляем или создаем запись времени блокировки
        stmt = insert(FacultyTimeDelta).values(
            faculty_id=faculty.id,
            hours_before_interview=hours
        ).on_conflict_do_update(
            index_elements=[FacultyTimeDelta.faculty_id],
            set_={"hours_before_interview": hours}
        )
        await session.execute(stmt)
        await session.commit()
        
        await callback.message.edit_text(
            f"✅ <b>Время блокировки обновлено!</b>\n\n"
            f"<b>Факультет:</b> {faculty.name}\n"
            f"<b>Новое значение:</b> {hours} часов\n\n"
            f"Теперь кандидаты не смогут записаться или отменить запись за {hours} часов до начала собеседования.",
            parse_mode="HTML"
        )

# --- Команда для отладки доступности собеседующих ---
@dp.message(Command("debug_availability"))
async def debug_availability(message: types.Message):
    tg_id = str(message.from_user.id)
    
    # Проверяем доступ - только для конкретного пользователя
    if tg_id != "922109605":
        await message.answer("У вас нет доступа к этой команде.")
        return
    
    await message.answer("🔍 Начинаю отладку доступности собеседующих...")
    
    try:
        async for session in get_session():
            # Получаем все факультеты
            result_faculties = await session.execute(select(Faculty))
            faculties = result_faculties.scalars().all()
            
            for faculty in faculties:
                if not faculty.google_sheet_url:
                    continue
                
                await message.answer(f"🏛️ <b>Факультет: {faculty.name}</b>", parse_mode="HTML")
                
                # Получаем всех собеседующих факультета
                result_sobesers = await session.execute(
                    select(User).where(
                        User.is_sobeser == True,
                        User.faculty_id == faculty.id
                    )
                )
                all_sobesers = result_sobesers.scalars().all()
                
                await message.answer(f"📋 Всего собеседующих: {len(all_sobesers)}")
                
                # Проверяем доступность для каждого собеседующего
                for sobeser in all_sobesers:
                    result_availability = await session.execute(
                        select(Availability).where(
                            Availability.user_id == sobeser.id,
                            Availability.faculty_id == faculty.id,
                            Availability.is_available == True
                        )
                    )
                    availabilities = result_availability.scalars().all()
                    
                    if availabilities:
                        dates_times = []
                        for avail in availabilities:
                            dates_times.append(f"{avail.date} {avail.time_slot}")
                        
                        await message.answer(
                            f"👤 <b>{sobeser.first_name} {sobeser.last_name}</b>\n"
                            f"Доступен в: {', '.join(dates_times[:5])}{'...' if len(dates_times) > 5 else ''}",
                            parse_mode="HTML"
                        )
                    else:
                        await message.answer(
                            f"👤 <b>{sobeser.first_name} {sobeser.last_name}</b> - НЕТ ДОСТУПНОСТИ",
                            parse_mode="HTML"
                        )
                
                await asyncio.sleep(1)  # Задержка между факультетами
                
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        await message.answer(f"❌ Ошибка при отладке:\n<pre>{e}\n{tb[-1000:]}</pre>")





async def main():
	await dp.start_polling(bot)

if __name__ == "__main__":
	asyncio.run(main())
