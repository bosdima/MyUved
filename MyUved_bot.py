import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from yadisk import AsyncClient as YandexClient

# Загружаем переменные окружения
load_dotenv()

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
YANDEX_TOKEN = os.getenv("YANDEX_TOKEN")
BACKUP_FOLDER = os.getenv("BACKUP_FOLDER", "TelegramBackups/")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", 5))
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 15))
NOTIFICATION_INTERVAL = int(os.getenv("NOTIFICATION_INTERVAL", 1))

# Настройка логов
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- Инициализация Яндекс.Диска ---
yandex_client = YandexClient(token=YANDEX_TOKEN)
BACKUP_PATH = Path("backups")
BACKUP_PATH.mkdir(exist_ok=True)

# --- Хранилище напоминаний (в реальном проекте используйте БД) ---
REMINDERS_FILE = "reminders.json"

def load_reminders():
    if os.path.exists(REMINDERS_FILE):
        with open(REMINDERS_FILE, 'r') as f:
            return json.load(f)
    return {"last_id": 0, "reminders": []}

def save_reminders(data):
    with open(REMINDERS_FILE, 'w') as f:
        json.dump(data, f, indent=4, default=str)

# --- Вспомогательные функции для Яндекс.Диска ---
async def check_yandex_token() -> bool:
    try:
        return await yandex_client.check_token()
    except Exception as e:
        logging.error(f"Ошибка проверки токена Яндекс.Диска: {e}")
        return False

async def upload_backup_to_yandex(file_path: Path) -> bool:
    """Загружает файл на Яндекс.Диск и возвращает True/False"""
    try:
        remote_path = f"{BACKUP_FOLDER}{file_path.name}"
        await yandex_client.upload(file_path, remote_path)
        logging.info(f"Бэкап {file_path.name} успешно загружен на Яндекс.Диск")
        return True
    except Exception as e:
        logging.error(f"Ошибка загрузки на Яндекс.Диск: {e}")
        return False

async def rotate_backups():
    """Оставляет только последние MAX_BACKUPS бэкапов на Диске"""
    try:
        files = []
        async for item in yandex_client.listdir(BACKUP_FOLDER):
            if item.is_file() and item.name.endswith(".json"):
                files.append((item.name, item.modified))
        # Сортируем по дате изменения (новые в конце)
        files.sort(key=lambda x: x[1])
        to_delete = files[:-MAX_BACKUPS] if len(files) > MAX_BACKUPS else []
        for name, _ in to_delete:
            await yandex_client.remove(f"{BACKUP_FOLDER}{name}", permanently=True)
            logging.info(f"Удален старый бэкап: {name}")
    except Exception as e:
        logging.error(f"Ошибка ротации бэкапов: {e}")

async def backup_reminders(force_notify=False):
    """Создает бэкап reminders.json, загружает на Диск и управляет ротацией"""
    file_path = BACKUP_PATH / REMINDERS_FILE
    # Сначала копируем текущие данные в файл
    with open(file_path, 'w') as f:
        json.dump(load_reminders(), f, indent=4, default=str)

    success = False
    attempt = 0
    while not success:
        success = await upload_backup_to_yandex(file_path)
        if not success:
            attempt += 1
            logging.warning(f"Попытка {attempt}: повторная загрузка бэкапа через {RETRY_INTERVAL} минут")
            await asyncio.sleep(RETRY_INTERVAL * 60)
        else:
            await rotate_backups()
            if force_notify:
                await bot.send_message(ADMIN_ID, f"✅ Бэкап успешно создан и загружен на Яндекс.Диск!\nВремя: {datetime.now()}")
            break

# --- Клавиатуры ---
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить напоминание", callback_data="add_reminder")],
        [InlineKeyboardButton(text="📋 Мои напоминания", callback_data="list_reminders")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")]
    ])

def get_duration_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Через 1 час", callback_data="duration_1_h"),
         InlineKeyboardButton(text="Через 3 часа", callback_data="duration_3_h")],
        [InlineKeyboardButton(text="Через 1 день", callback_data="duration_1_d"),
         InlineKeyboardButton(text="Через 3 дня", callback_data="duration_3_d")],
        [InlineKeyboardButton(text="Через 1 месяц", callback_data="duration_1_m"),
         InlineKeyboardButton(text="Выбрать дату", callback_data="duration_custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

def get_reminder_actions(reminder_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete_{reminder_id}"),
         InlineKeyboardButton(text="⏰ Отсрочить", callback_data=f"snooze_{reminder_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{reminder_id}")]
    ])

def get_snooze_keyboard(reminder_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 час", callback_data=f"snooze_1h_{reminder_id}"),
         InlineKeyboardButton(text="3 часа", callback_data=f"snooze_3h_{reminder_id}")],
        [InlineKeyboardButton(text="1 день", callback_data=f"snooze_1d_{reminder_id}"),
         InlineKeyboardButton(text="3 дня", callback_data=f"snooze_3d_{reminder_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_{reminder_id}")]
    ])

# --- FSM для создания напоминания ---
class ReminderForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_duration = State()
    waiting_for_custom_date = State()

# --- Обработчики команд и сообщений ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "📌 *Ваш персональный бот-напоминалка*\\!\n\n"
        "Я буду напоминать вам о важных событиях и автоматически сохранять бэкапы на Яндекс\\.Диск\\.",
        reply_markup=get_main_keyboard(),
        parse_mode="MarkdownV2"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("📌 *Главное меню*", reply_markup=get_main_keyboard(), parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(F.data == "add_reminder")
async def add_reminder_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✏️ Введите текст напоминания:")
    await state.set_state(ReminderForm.waiting_for_text)
    await callback.answer()

@dp.message(ReminderForm.waiting_for_text)
async def process_reminder_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await message.answer("⏰ Выберите, когда напомнить:", reply_markup=get_duration_keyboard())
    await state.set_state(ReminderForm.waiting_for_duration)

@dp.callback_query(ReminderForm.waiting_for_duration, F.data.startswith("duration_"))
async def process_duration(callback: CallbackQuery, state: FSMContext):
    data = callback.data.split("_")
    duration_type = data[1]
    duration_value = data[2] if len(data) > 2 else None

    remind_time = datetime.now()
    if duration_type == "h":
        remind_time += timedelta(hours=int(duration_value))
    elif duration_type == "d":
        remind_time += timedelta(days=int(duration_value))
    elif duration_type == "m":
        remind_time += timedelta(days=30)  # Аппроксимация месяца
    elif duration_type == "custom":
        await callback.message.edit_text("📅 Введите дату и время в формате: *ГГГГ-ММ-ДД ЧЧ:ММ*\\, например *2025-12-31 23:59*", parse_mode="MarkdownV2")
        await state.set_state(ReminderForm.waiting_for_custom_date)
        await callback.answer()
        return

    await save_reminder(callback, state, remind_time)

async def save_reminder(callback: CallbackQuery, state: FSMContext, remind_time: datetime):
    user_data = await state.get_data()
    text = user_data.get("text")
    reminders_data = load_reminders()
    new_id = reminders_data["last_id"] + 1
    reminders_data["last_id"] = new_id
    reminders_data["reminders"].append({
        "id": new_id,
        "user_id": callback.from_user.id,
        "text": text,
        "remind_at": remind_time.isoformat(),
        "is_active": True,
        "confirmed": False,
        "last_notified": None
    })
    save_reminders(reminders_data)
    await callback.message.edit_text(f"✅ Напоминание создано!\n\nТекст: {text}\nНапомню: {remind_time.strftime('%Y-%m-%d %H:%M')}")
    await state.clear()
    await callback.answer()
    # Делаем бэкап после изменения
    asyncio.create_task(backup_reminders(force_notify=True))

@dp.message(ReminderForm.waiting_for_custom_date)
async def process_custom_date(message: Message, state: FSMContext):
    try:
        remind_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        if remind_time < datetime.now():
            await message.answer("❌ Нельзя установить время в прошлом. Попробуйте снова:")
            return
        user_data = await state.get_data()
        reminders_data = load_reminders()
        new_id = reminders_data["last_id"] + 1
        reminders_data["last_id"] = new_id
        reminders_data["reminders"].append({
            "id": new_id,
            "user_id": message.from_user.id,
            "text": user_data["text"],
            "remind_at": remind_time.isoformat(),
            "is_active": True,
            "confirmed": False,
            "last_notified": None
        })
        save_reminders(reminders_data)
        await message.answer(f"✅ Напоминание создано!\n\nТекст: {user_data['text']}\nНапомню: {remind_time.strftime('%Y-%m-%d %H:%M')}")
        await state.clear()
        asyncio.create_task(backup_reminders(force_notify=True))
    except ValueError:
        await message.answer("❌ Неверный формат. Введите дату в формате: *ГГГГ-ММ-ДД ЧЧ:ММ*", parse_mode="MarkdownV2")

@dp.callback_query(F.data == "list_reminders")
async def list_reminders(callback: CallbackQuery):
    reminders_data = load_reminders()
    user_reminders = [r for r in reminders_data["reminders"] if r["user_id"] == callback.from_user.id and r["is_active"]]
    if not user_reminders:
        await callback.message.edit_text("У вас пока нет активных напоминаний.", reply_markup=get_main_keyboard())
        await callback.answer()
        return
    text = "📋 *Ваши напоминания:*\n\n"
    for r in user_reminders:
        remind_time = datetime.fromisoformat(r["remind_at"])
        text += f"ID: {r['id']}\n📝 {r['text']}\n⏰ {remind_time.strftime('%Y-%m-%d %H:%M')}\n➖➖➖➖➖➖➖\n"
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(F.data.startswith("complete_"))
async def complete_reminder(callback: CallbackQuery):
    reminder_id = int(callback.data.split("_")[1])
    reminders_data = load_reminders()
    for r in reminders_data["reminders"]:
        if r["id"] == reminder_id and r["user_id"] == callback.from_user.id:
            r["is_active"] = False
            r["confirmed"] = True
            save_reminders(reminders_data)
            await callback.message.edit_text(f"✅ Напоминание «{r['text']}» выполнено! Отличная работа!")
            asyncio.create_task(backup_reminders(force_notify=True))
            break
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_"))
async def snooze_menu(callback: CallbackQuery):
    parts = callback.data.split("_")
    reminder_id = int(parts[1])
    await callback.message.edit_text("⏰ На сколько отсрочить напоминание?", reply_markup=get_snooze_keyboard(reminder_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_1h_") or F.data.startswith("snooze_3h_") or F.data.startswith("snooze_1d_") or F.data.startswith("snooze_3d_"))
async def apply_snooze(callback: CallbackQuery):
    parts = callback.data.split("_")
    duration = parts[1]  # 1h, 3h, 1d, 3d
    reminder_id = int(parts[2])
    reminders_data = load_reminders()
    for r in reminders_data["reminders"]:
        if r["id"] == reminder_id and r["user_id"] == callback.from_user.id:
            old_time = datetime.fromisoformat(r["remind_at"])
            if duration.endswith("h"):
                new_time = old_time + timedelta(hours=int(duration[:-1]))
            else:
                new_time = old_time + timedelta(days=int(duration[:-1]))
            r["remind_at"] = new_time.isoformat()
            save_reminders(reminders_data)
            await callback.message.edit_text(f"⏰ Напоминание отсрочено до {new_time.strftime('%Y-%m-%d %H:%M')}")
            asyncio.create_task(backup_reminders(force_notify=True))
            break
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def delete_reminder(callback: CallbackQuery):
    reminder_id = int(callback.data.split("_")[1])
    reminders_data = load_reminders()
    reminders_data["reminders"] = [r for r in reminders_data["reminders"] if not (r["id"] == reminder_id and r["user_id"] == callback.from_user.id)]
    save_reminders(reminders_data)
    await callback.message.edit_text("🗑 Напоминание удалено.")
    asyncio.create_task(backup_reminders(force_notify=True))
    await callback.answer()

# --- Фоновые задачи ---
async def check_reminders():
    """Проверяет, какие напоминания пора отправить"""
    now = datetime.now()
    reminders_data = load_reminders()
    updated = False
    for r in reminders_data["reminders"]:
        if r["is_active"] and not r.get("confirmed", False):
            remind_time = datetime.fromisoformat(r["remind_at"])
            if remind_time <= now:
                # Отправляем уведомление
                keyboard = get_reminder_actions(r["id"])
                try:
                    await bot.send_message(
                        r["user_id"],
                        f"🔔 *Напоминание!*\n\n{r['text']}",
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                    r["last_notified"] = now.isoformat()
                    # Если не подтверждено, переносим на час (задано в настройках)
                    r["remind_at"] = (now + timedelta(hours=NOTIFICATION_INTERVAL)).isoformat()
                    updated = True
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление пользователю {r['user_id']}: {e}")
    if updated:
        save_reminders(reminders_data)
        # Бэкапим только если были изменения
        asyncio.create_task(backup_reminders(force_notify=False))

async def daily_yandex_check():
    """Ежедневная проверка Яндекс.Диска в 6:00"""
    if await check_yandex_token():
        msg = await bot.send_message(ADMIN_ID, "✅ Подключение к Яндекс.Диску стабильно, токен валиден.")
        await asyncio.sleep(5)
        await bot.delete_message(ADMIN_ID, msg.message_id)
    else:
        await bot.send_message(ADMIN_ID, "⚠️ Ошибка! Токен Яндекс.Диска недействителен или истек.")

# --- Запуск планировщика ---
async def on_startup():
    # Проверяем токен Яндекс.Диска при старте
    if await check_yandex_token():
        await bot.send_message(ADMIN_ID, "✅ Бот запущен и подключен к Яндекс.Диску.")
    else:
        await bot.send_message(ADMIN_ID, "⚠️ ВНИМАНИЕ! Бот запущен, но НЕТ ДОСТУПА к Яндекс.Диску. Проверьте токен.")
    # Запускаем планировщик
    scheduler.add_job(daily_yandex_check, CronTrigger(hour=6, minute=0))
    scheduler.add_job(check_reminders, IntervalTrigger(seconds=30))
    scheduler.start()

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main()