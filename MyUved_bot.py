import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

# Версия бота
BOT_VERSION = "2.0.1"
BOT_DATE = "2026-04-03"

# Загружаем переменные окружения
load_dotenv()

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None
YANDEX_TOKEN = os.getenv("YANDEX_TOKEN")
BACKUP_FOLDER = os.getenv("BACKUP_FOLDER", "TelegramBackups/")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", 5))
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL", 15))
NOTIFICATION_INTERVAL = int(os.getenv("NOTIFICATION_INTERVAL", 1))

# Проверка наличия обязательных переменных
if not BOT_TOKEN:
    logging.error("BOT_TOKEN не найден в .env файле!")
    sys.exit(1)
if not ADMIN_ID:
    logging.error("ADMIN_ID не найден в .env файле!")
    sys.exit(1)

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# --- Инициализация Яндекс.Диска (опционально) ---
yandex_client = None
if YANDEX_TOKEN:
    try:
        from yadisk import AsyncClient as YandexClient
        yandex_client = YandexClient(token=YANDEX_TOKEN)
        logger.info("Яндекс.Диск инициализирован")
    except ImportError:
        logger.warning("Библиотека yadisk не установлена. Бэкапы на Яндекс.Диск не будут работать")
    except Exception as e:
        logger.error(f"Ошибка инициализации Яндекс.Диска: {e}")

BACKUP_PATH = Path("backups")
BACKUP_PATH.mkdir(exist_ok=True)

# --- Хранилище напоминаний ---
REMINDERS_FILE = "reminders.json"

def load_reminders():
    """Загружает напоминания из файла"""
    try:
        if os.path.exists(REMINDERS_FILE):
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки напоминаний: {e}")
    return {"last_id": 0, "reminders": []}

def save_reminders(data):
    """Сохраняет напоминания в файл"""
    try:
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False, default=str)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения напоминаний: {e}")
        return False

# --- Вспомогательные функции для Яндекс.Диска ---
async def check_yandex_token() -> bool:
    """Проверяет валидность токена Яндекс.Диска"""
    if not yandex_client:
        return False
    try:
        return await yandex_client.check_token()
    except Exception as e:
        logger.error(f"Ошибка проверки токена Яндекс.Диска: {e}")
        return False

async def ensure_backup_folder():
    """Создает папку для бэкапов на Яндекс.Диске, если её нет"""
    if not yandex_client:
        return False
    try:
        try:
            await yandex_client.get_meta(BACKUP_FOLDER.rstrip('/'))
        except:
            await yandex_client.mkdir(BACKUP_FOLDER.rstrip('/'))
            logger.info(f"Создана папка {BACKUP_FOLDER} на Яндекс.Диске")
        return True
    except Exception as e:
        logger.error(f"Ошибка создания папки на Яндекс.Диске: {e}")
        return False

async def upload_backup_to_yandex(file_path: Path) -> bool:
    """Загружает файл на Яндекс.Диск и возвращает True/False"""
    if not yandex_client:
        return False
    try:
        await ensure_backup_folder()
        remote_path = f"{BACKUP_FOLDER}{file_path.name}"
        await yandex_client.upload(file_path, remote_path)
        logger.info(f"Бэкап {file_path.name} успешно загружен на Яндекс.Диск")
        return True
    except Exception as e:
        logger.error(f"Ошибка загрузки на Яндекс.Диск: {e}")
        return False

async def rotate_backups():
    """Оставляет только последние MAX_BACKUPS бэкапов на Диске"""
    if not yandex_client:
        return
    try:
        files = []
        async for item in yandex_client.listdir(BACKUP_FOLDER):
            if item.is_file() and item.name.endswith(".json"):
                files.append((item.name, item.modified))
        files.sort(key=lambda x: x[1])
        to_delete = files[:-MAX_BACKUPS] if len(files) > MAX_BACKUPS else []
        for name, _ in to_delete:
            await yandex_client.remove(f"{BACKUP_FOLDER}{name}", permanently=True)
            logger.info(f"Удален старый бэкап: {name}")
    except Exception as e:
        logger.error(f"Ошибка ротации бэкапов: {e}")

async def backup_reminders(force_notify=False):
    """Создает бэкап reminders.json, загружает на Диск и управляет ротацией"""
    file_path = BACKUP_PATH / REMINDERS_FILE
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(load_reminders(), f, indent=4, ensure_ascii=False, default=str)

    if not yandex_client:
        logger.info("Яндекс.Диск не настроен. Бэкап сохранен локально.")
        return

    success = False
    attempt = 0
    while not success and attempt < 5:  # Ограничиваем количество попыток
        success = await upload_backup_to_yandex(file_path)
        if not success:
            attempt += 1
            logger.warning(f"Попытка {attempt}: повторная загрузка бэкапа через {RETRY_INTERVAL} минут")
            await asyncio.sleep(RETRY_INTERVAL * 60)
        else:
            await rotate_backups()
            if force_notify:
                await bot.send_message(ADMIN_ID, f"✅ Бэкап успешно создан и загружен на Яндекс.Диск!\nВремя: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            break

# --- Клавиатуры ---
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить напоминание", callback_data="add_reminder")],
        [InlineKeyboardButton(text="📋 Мои напоминания", callback_data="list_reminders")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
    ])

def get_duration_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 час", callback_data="duration_1_h"),
         InlineKeyboardButton(text="3 часа", callback_data="duration_3_h")],
        [InlineKeyboardButton(text="6 часов", callback_data="duration_6_h"),
         InlineKeyboardButton(text="12 часов", callback_data="duration_12_h")],
        [InlineKeyboardButton(text="1 день", callback_data="duration_1_d"),
         InlineKeyboardButton(text="3 дня", callback_data="duration_3_d")],
        [InlineKeyboardButton(text="1 неделя", callback_data="duration_1_w"),
         InlineKeyboardButton(text="1 месяц", callback_data="duration_1_m")],
        [InlineKeyboardButton(text="📅 Выбрать дату", callback_data="duration_custom")],
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
        [InlineKeyboardButton(text="1 неделя", callback_data=f"snooze_1w_{reminder_id}")],
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
        f"🤖 Версия: `{BOT_VERSION}` от {BOT_DATE}\n\n"
        "Я буду напоминать вам о важных событиях и автоматически сохранять бэкапы на Яндекс\\.Диск\\.",
        reply_markup=get_main_keyboard(),
        parse_mode="MarkdownV2"
    )
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@dp.message(Command("version"))
async def cmd_version(message: Message):
    await message.answer(
        f"🤖 *Информация о боте*\n\n"
        f"📌 Название: MyUved Bot\n"
        f"🔢 Версия: `{BOT_VERSION}`\n"
        f"📅 Дата выпуска: {BOT_DATE}\n"
        f"🐍 Python: {sys.version.split()[0]}\n"
        f"👤 Админ: `{ADMIN_ID}`",
        parse_mode="MarkdownV2"
    )

@dp.callback_query(F.data == "about")
async def about_bot(callback: CallbackQuery):
    await callback.message.edit_text(
        f"🤖 *О боте*\n\n"
        f"📌 Название: MyUved Bot\n"
        f"🔢 Версия: `{BOT_VERSION}`\n"
        f"📅 Дата выпуска: {BOT_DATE}\n\n"
        f"*Функции:*\n"
        f"• 📝 Создание напоминаний\n"
        f"• 💾 Автоматический бэкап на Яндекс.Диск\n"
        f"• 🔄 Ротация бэкапов (последние {MAX_BACKUPS})\n"
        f"• ⏰ Повтор уведомлений каждые {NOTIFICATION_INTERVAL} час(ов)\n\n"
        f"👨‍💻 Разработчик: @your_username",
        reply_markup=get_main_keyboard(),
        parse_mode="MarkdownV2"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "📌 *Главное меню*",
        reply_markup=get_main_keyboard(),
        parse_mode="MarkdownV2"
    )
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
    elif duration_type == "w":
        remind_time += timedelta(weeks=int(duration_value))
    elif duration_type == "m":
        remind_time += timedelta(days=30)
    elif duration_type == "custom":
        await callback.message.edit_text(
            "📅 Введите дату и время в формате: *ГГГГ-ММ-ДД ЧЧ:ММ*\\, например *2025-12-31 23:59*",
            parse_mode="MarkdownV2"
        )
        await state.set_state(ReminderForm.waiting_for_custom_date)
        await callback.answer()
        return

    await save_reminder_callback(callback, state, remind_time)

async def save_reminder_callback(callback: CallbackQuery, state: FSMContext, remind_time: datetime):
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
    await callback.message.edit_text(
        f"✅ Напоминание создано!\n\n"
        f"📝 Текст: {text}\n"
        f"⏰ Напомню: {remind_time.strftime('%Y-%m-%d %H:%M')}"
    )
    await state.clear()
    await callback.answer()
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
        await message.answer(
            f"✅ Напоминание создано!\n\n"
            f"📝 Текст: {user_data['text']}\n"
            f"⏰ Напомню: {remind_time.strftime('%Y-%m-%d %H:%M')}"
        )
        await state.clear()
        asyncio.create_task(backup_reminders(force_notify=True))
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Введите дату в формате: *ГГГГ-ММ-ДД ЧЧ:ММ*\n"
            "Пример: `2025-12-31 23:59`",
            parse_mode="MarkdownV2"
        )

@dp.callback_query(F.data == "list_reminders")
async def list_reminders(callback: CallbackQuery):
    reminders_data = load_reminders()
    user_reminders = [r for r in reminders_data["reminders"] if r["user_id"] == callback.from_user.id and r["is_active"]]
    
    if not user_reminders:
        await callback.message.edit_text(
            "📭 У вас пока нет активных напоминаний.\n\n"
            "Нажмите «➕ Добавить напоминание», чтобы создать первое!",
            reply_markup=get_main_keyboard()
        )
        await callback.answer()
        return
    
    text = "📋 *Ваши активные напоминания:*\n\n"
    for r in user_reminders:
        remind_time = datetime.fromisoformat(r["remind_at"])
        text += f"🆔 ID: `{r['id']}`\n"
        text += f"📝 {r['text']}\n"
        text += f"⏰ {remind_time.strftime('%d.%m.%Y %H:%M')}\n"
        text += "➖➖➖➖➖➖➖\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="list_reminders")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(F.data == "settings")
async def settings_menu(callback: CallbackQuery):
    yandex_status = "✅ Подключен" if await check_yandex_token() else "❌ Не подключен"
    
    settings_text = (
        "⚙️ *Настройки бота*\n\n"
        f"📁 Папка бэкапов: `{BACKUP_FOLDER}`\n"
        f"💾 Максимум бэкапов: `{MAX_BACKUPS}`\n"
        f"🔄 Интервал повтора загрузки: `{RETRY_INTERVAL}` мин\n"
        f"⏰ Интервал уведомлений: `{NOTIFICATION_INTERVAL}` час\n"
        f"☁️ Яндекс.Диск: {yandex_status}\n\n"
        "📤 *Экспорт данных:* Сохранить все напоминания в файл\n"
        "📥 *Импорт данных:* Восстановить напоминания из файла"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Экспорт данных", callback_data="export_data")],
        [InlineKeyboardButton(text="📥 Импорт данных", callback_data="import_data")],
        [InlineKeyboardButton(text="💾 Ручной бэкап", callback_data="manual_backup")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(F.data == "manual_backup")
async def manual_backup(callback: CallbackQuery):
    await callback.message.edit_text("🔄 Создаю резервную копию...")
    await backup_reminders(force_notify=True)
    await callback.answer()

@dp.callback_query(F.data == "export_data")
async def export_data(callback: CallbackQuery):
    file_path = BACKUP_PATH / REMINDERS_FILE
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(load_reminders(), f, indent=4, ensure_ascii=False, default=str)
    
    document = FSInputFile(file_path, filename=f"reminders_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    await callback.message.answer_document(document, caption="📁 Ваши данные (напоминания)")
    await callback.answer()

@dp.callback_query(F.data == "import_data")
async def import_data(callback: CallbackQuery):
    await callback.message.answer(
        "📥 Отправьте JSON-файл с данными для импорта.\n\n"
        "⚠️ Внимание: текущие напоминания будут заменены!"
    )
    await callback.answer()

@dp.message(F.document)
async def handle_import_file(message: Message):
    if message.document.file_name.endswith('.json'):
        file = await bot.get_file(message.document.file_id)
        file_path = BACKUP_PATH / f"imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        await bot.download_file(file.file_path, destination=file_path)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_data = json.load(f)
            
            if "reminders" in imported_data and "last_id" in imported_data:
                save_reminders(imported_data)
                await message.answer(f"✅ Данные успешно импортированы!\n\n📊 Загружено напоминаний: {len(imported_data['reminders'])}")
                asyncio.create_task(backup_reminders(force_notify=True))
            else:
                await message.answer("❌ Неверный формат файла. Файл должен содержать 'reminders' и 'last_id'.")
        except Exception as e:
            await message.answer(f"❌ Ошибка импорта: {e}")
    else:
        await message.answer("❌ Пожалуйста, отправьте JSON файл.")

@dp.callback_query(F.data.startswith("complete_"))
async def complete_reminder(callback: CallbackQuery):
    reminder_id = int(callback.data.split("_")[1])
    reminders_data = load_reminders()
    
    for r in reminders_data["reminders"]:
        if r["id"] == reminder_id and r["user_id"] == callback.from_user.id:
            r["is_active"] = False
            r["confirmed"] = True
            save_reminders(reminders_data)
            await callback.message.edit_text(f"✅ Напоминание выполнено!\n\n📝 {r['text']}")
            asyncio.create_task(backup_reminders(force_notify=True))
            break
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_") & ~F.data.startswith("snooze_1h_") & ~F.data.startswith("snooze_3h_") & ~F.data.startswith("snooze_1d_") & ~F.data.startswith("snooze_3d_") & ~F.data.startswith("snooze_1w_"))
async def snooze_menu(callback: CallbackQuery):
    parts = callback.data.split("_")
    reminder_id = int(parts[1])
    await callback.message.edit_text("⏰ На сколько отсрочить напоминание?", reply_markup=get_snooze_keyboard(reminder_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze_1h_") | F.data.startswith("snooze_3h_") | F.data.startswith("snooze_1d_") | F.data.startswith("snooze_3d_") | F.data.startswith("snooze_1w_"))
async def apply_snooze(callback: CallbackQuery):
    parts = callback.data.split("_")
    duration = parts[1]
    reminder_id = int(parts[2])
    reminders_data = load_reminders()
    
    for r in reminders_data["reminders"]:
        if r["id"] == reminder_id and r["user_id"] == callback.from_user.id:
            old_time = datetime.fromisoformat(r["remind_at"])
            
            if duration.endswith("h"):
                new_time = old_time + timedelta(hours=int(duration[:-1]))
            elif duration.endswith("d"):
                new_time = old_time + timedelta(days=int(duration[:-1]))
            elif duration.endswith("w"):
                new_time = old_time + timedelta(weeks=int(duration[:-1]))
            else:
                new_time = old_time + timedelta(hours=1)
            
            r["remind_at"] = new_time.isoformat()
            save_reminders(reminders_data)
            await callback.message.edit_text(
                f"⏰ Напоминание отсрочено!\n\n"
                f"📝 {r['text']}\n"
                f"🕐 Новое время: {new_time.strftime('%Y-%m-%d %H:%M')}"
            )
            asyncio.create_task(backup_reminders(force_notify=True))
            break
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def delete_reminder(callback: CallbackQuery):
    reminder_id = int(callback.data.split("_")[1])
    reminders_data = load_reminders()
    
    reminders_data["reminders"] = [r for r in reminders_data["reminders"] if not (r["id"] == reminder_id and r["user_id"] == callback.from_user.id)]
    
    if save_reminders(reminders_data):
        await callback.message.edit_text("🗑 Напоминание удалено.")
        asyncio.create_task(backup_reminders(force_notify=True))
    else:
        await callback.message.edit_text("❌ Ошибка при удалении напоминания.")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("back_to_"))
async def back_to_reminder(callback: CallbackQuery):
    reminder_id = int(callback.data.split("_")[2])
    reminders_data = load_reminders()
    
    for r in reminders_data["reminders"]:
        if r["id"] == reminder_id:
            await callback.message.edit_text(
                f"🔔 *Напоминание*\n\n{r['text']}",
                reply_markup=get_reminder_actions(reminder_id),
                parse_mode="MarkdownV2"
            )
            break
    await callback.answer()

# --- Фоновые задачи ---
async def check_reminders():
    """Проверяет, какие напоминания пора отправить"""
    now = datetime.now()
    reminders_data = load_reminders()
    updated = False
    
    for r in reminders_data["reminders"]:
        if r.get("is_active") and not r.get("confirmed", False):
            try:
                remind_time = datetime.fromisoformat(r["remind_at"])
                if remind_time <= now:
                    keyboard = get_reminder_actions(r["id"])
                    await bot.send_message(
                        r["user_id"],
                        f"🔔 *Напоминание!*\n\n{r['text']}",
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                    r["last_notified"] = now.isoformat()
                    r["remind_at"] = (now + timedelta(hours=NOTIFICATION_INTERVAL)).isoformat()
                    updated = True
                    logger.info(f"Отправлено напоминание #{r['id']} пользователю {r['user_id']}")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление: {e}")
    
    if updated:
        save_reminders(reminders_data)
        asyncio.create_task(backup_reminders(force_notify=False))

async def daily_yandex_check():
    """Ежедневная проверка Яндекс.Диска в 6:00"""
    logger.info("Выполняется ежедневная проверка Яндекс.Диска")
    if await check_yandex_token():
        msg = await bot.send_message(ADMIN_ID, "✅ Подключение к Яндекс.Диску стабильно, токен валиден.")
        await asyncio.sleep(5)
        await bot.delete_message(ADMIN_ID, msg.message_id)
    else:
        await bot.send_message(ADMIN_ID, "⚠️ Ошибка! Токен Яндекс.Диска недействителен или истек.")

# --- Запуск планировщика ---
async def on_startup():
    """Действия при запуске бота"""
    logger.info(f"Запуск бота версии {BOT_VERSION} от {BOT_DATE}")
    
    yandex_status = await check_yandex_token()
    if yandex_status:
        await bot.send_message(ADMIN_ID, f"✅ Бот v{BOT_VERSION} запущен и подключен к Яндекс.Диску.")
        logger.info("Бот подключен к Яндекс.Диску")
    else:
        await bot.send_message(ADMIN_ID, f"⚠️ Бот v{BOT_VERSION} запущен, но НЕТ ДОСТУПА к Яндекс.Диску. Проверьте токен.")
        logger.warning("Нет доступа к Яндекс.Диску")
    
    scheduler.add_job(daily_yandex_check, CronTrigger(hour=6, minute=0))
    scheduler.add_job(check_reminders, IntervalTrigger(seconds=30))
    scheduler.start()
    
    logger.info("Планировщик задач запущен")

async def main():
    """Основная функция запуска бота"""
    await on_startup()
    logger.info("Бот начал polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)