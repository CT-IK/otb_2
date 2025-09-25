# Простой Telegram-бот на aiogram, который определяет роль пользователя по базе данных
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram import F
import asyncio
from db.engine import get_session
from db.models import User, Faculty, Candidate, Availability
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from dotenv import load_dotenv
import gspread
import traceback

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

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
                    id=next_id,
                    first_name=first_name,
                    last_name=last_name,
                    is_sobeser=True,
                    faculty_id=faculty.id
                )
                await session.execute(stmt)
                next_id += 1
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
                    id=next_id,
                    first_name=first_name,
                    last_name=last_name,
                    is_sobeser=True,
                    faculty_id=faculty.id
                )
                await session.execute(stmt)
                next_id += 1
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
                    # user_id хранится в A15
                    user_id_cell = ws.acell("A15").value
                    if not user_id_cell:
                        continue
                    user_id = int(user_id_cell)
                    # Даты в B1:H1
                    dates = ws.range("B1:H1")
                    date_values = [cell.value for cell in dates]
                    # Временные интервалы в A2:A13
                    times = ws.range("A2:A13")
                    time_values = [cell.value for cell in times]
                    # Парсим B2:H13
                    grid = ws.range("B2:H13")
                    for i, cell in enumerate(grid):
                        row = i // 8  # 8 столбцов
                        col = i % 8
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

async def main():
	await dp.start_polling(bot)

if __name__ == "__main__":
	asyncio.run(main())
