
# --- Обработчик кнопки 'Назад' на этапе выбора времени ---

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
from db.models import User, Faculty, Candidate, Availability, SlotLimit, InterviewRegistration

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv
import gspread
import traceback
import redis.asyncio as redis

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

class VKAuth(StatesGroup):
    waiting_vk_id = State()

# --- Хэндлер старта для обычных пользователей ---
@dp.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user and (user.is_admin_faculty or user.is_sobeser):
            await message.answer("Вы уже авторизованы как сотрудник.")
            return
    await message.answer("Пожалуйста, введите ваш VK ID для регистрации:")
    await state.set_state(VKAuth.waiting_vk_id)

# --- Хэндлер ввода VK ID ---
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

# --- Callback Да ---
@dp.callback_query(F.data.startswith("vk_yes_"))
async def vk_yes_callback(call: CallbackQuery):
    candidate_id = int(call.data.split("_")[-1])
    async for session in get_session():
        candidate = await session.scalar(select(Candidate).where(Candidate.id == candidate_id))
        if not candidate:
            await call.message.answer("Ошибка: кандидат не найден.")
            return
        # Проверяем, есть ли уже пользователь с этим tg_id
        user = await session.scalar(select(User).where(User.tg_id == str(call.from_user.id)))
        if not user:
            session.add(User(
                tg_id=str(call.from_user.id),
                first_name=candidate.first_name,
                last_name=candidate.last_name,
                faculty_id=candidate.faculty_id
            ))
            await session.commit()
    await call.message.answer("Вы успешно зарегистрированы!")
    await call.message.edit_reply_markup()

# --- Callback Нет ---
@dp.callback_query(F.data == "vk_no")
async def vk_no_callback(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Пожалуйста, введите ваш VK ID ещё раз:")
    await call.message.edit_reply_markup()
    await state.set_state(VKAuth.waiting_vk_id)


class InterviewFSM(StatesGroup):
    choosing_date = State()
    choosing_time = State()


# --- Меню кандидата ---
@dp.message(Command("menu"))
async def candidate_menu(message: types.Message):
    tg_id = str(message.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await message.answer("Вы не зарегистрированы как кандидат.")
            return
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
        result = await session.execute(
            select(SlotLimit.date).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.limit > 0
            ).distinct()
        )
        dates = [r[0] for r in result.all()]
        if not dates:
            await callback.message.edit_text("Нет доступных дат для записи.")
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
        # Получаем доступные интервалы времени с лимитом > 0
        result = await session.execute(
            select(SlotLimit.time_slot).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.date == date,
                SlotLimit.limit > 0
            )
        )
        time_slots = [r[0] for r in result.all()]
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
        # Уведомляем админа факультета
        admin = await session.scalar(select(User).where(User.id == (await session.scalar(select(Faculty.admin_id).where(Faculty.id == faculty_id)))))
        if admin:
            try:
                await bot.send_message(admin.tg_id, f"Кандидат {user.first_name} {user.last_name} записался на собеседование: {date} {time_slot}")
            except Exception:
                pass
        # Показываем меню кандидата с кнопкой записи
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Записаться на собеседование", callback_data="register_interview")]
            ]
        )
        await callback.message.edit_text(f"Вы успешно записаны на собеседование: {date} {time_slot}", reply_markup=kb)
        await state.clear()


@dp.callback_query(InterviewFSM.choosing_time, F.data == "reg_back_to_dates")
async def register_interview_back_to_dates(callback: CallbackQuery, state: FSMContext):
    tg_id = str(callback.from_user.id)
    async for session in get_session():
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user or not user.is_candidate:
            await callback.message.edit_text("Вы не зарегистрированы как кандидат.")
            return
        faculty_id = user.faculty_id
        result = await session.execute(
            select(SlotLimit.date).where(
                SlotLimit.faculty_id == faculty_id,
                SlotLimit.limit > 0
            ).distinct()
        )
        dates = [r[0] for r in result.all()]
        if not dates:
            await callback.message.edit_text("Нет доступных дат для записи.")
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=date, callback_data=f"reg_date:{date}")] for date in dates
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
        time_slots = [r[0] for r in result.all()]
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
            [InlineKeyboardButton(text="Назад", callback_data=f"slot_date:{date}")]
        ])
        text = (
            f"<b>{date} — {time_slot}</b>\n\n"
            f"<b>Доступные люди:</b>\n{user_list}\n\n"
            f"<b>Максимальное количество слотов для записи:</b> <b>{current_slots}</b>\n\n"
            f"Выберите лимит с помощью кнопок ниже."
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

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

async def main():
	await dp.start_polling(bot)

if __name__ == "__main__":
	asyncio.run(main())
