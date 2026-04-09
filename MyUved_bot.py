import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode
import pytz
import re

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, InputFile
from aiogram.utils import executor
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_debug.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Версия бота
BOT_VERSION = "3.30"
BOT_VERSION_DATE = "09.04.2026"
BOT_VERSION_TIME = "17:00"

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

# Проверка
if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET]):
    logger.error("❌ Ошибка: Не все переменные окружения заданы!")
    exit(1)

logger.info(f"BOT_TOKEN: {BOT_TOKEN[:10]}...")
logger.info(f"CLIENT_ID: {CLIENT_ID}")
logger.info(f"ADMIN_ID: {ADMIN_ID}")

# Инициализация бота
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Файлы для хранения данных
DATA_FILE = 'notifications.json'
CONFIG_FILE = 'config.json'
TOKEN_FILE = 'user_tokens.json'
BACKUP_DIR = 'backups'

# Глобальные переменные
notifications: Dict = {}
config: Dict = {}
user_tokens: Dict[int, str] = {}
notifications_enabled = True

# URL для API
YANDEX_API_BASE = "https://cloud-api.yandex.net/v1/disk"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_URL = "https://oauth.yandex.ru/token"

# Часовые пояса
TIMEZONES = {
    'Москва (UTC+3)': 'Europe/Moscow',
    'Санкт-Петербург (UTC+3)': 'Europe/Moscow',
    'Калининград (UTC+2)': 'Europe/Kaliningrad',
    'Екатеринбург (UTC+5)': 'Asia/Yekaterinburg',
    'Новосибирск (UTC+7)': 'Asia/Novosibirsk',
    'Красноярск (UTC+7)': 'Asia/Krasnoyarsk',
    'Иркутск (UTC+8)': 'Asia/Irkutsk',
    'Владивосток (UTC+10)': 'Asia/Vladivostok',
    'Магадан (UTC+11)': 'Asia/Magadan',
    'Камчатка (UTC+12)': 'Asia/Kamchatka'
}

WEEKDAYS_BUTTONS = [("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)]
WEEKDAYS_NAMES = {0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"}


def get_current_time():
    timezone_str = config.get('timezone', 'Europe/Moscow')
    tz = pytz.timezone(timezone_str)
    return datetime.now(tz)


def get_auth_url() -> str:
    """Возвращает URL для авторизации"""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "cloud_api:disk.write cloud_api:disk.read"
    }
    return f"{YANDEX_OAUTH_URL}?{urlencode(params)}"


async def get_access_token(auth_code: str) -> Optional[str]:
    """Получает access token по коду авторизации"""
    url = YANDEX_TOKEN_URL
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=data) as response:
                response_text = await response.text()
                logger.info(f"Token response status: {response.status}")
                
                if response.status == 200:
                    result = await response.json()
                    token = result.get("access_token")
                    if token:
                        logger.info(f"✅ Token получен, длина: {len(token)}")
                        return token
                    else:
                        logger.error("Нет access_token в ответе")
                        return None
                else:
                    logger.error(f"Ошибка получения токена: {response_text}")
                    return None
        except Exception as e:
            logger.error(f"Исключение: {e}")
            return None


class YandexDiskAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = YANDEX_API_BASE
        self.headers = {"Authorization": f"OAuth {token}", "Content-Type": "application/json"}
    
    async def check_access(self):
        try:
            url = f"{self.base_url}/"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status != 200:
                        return False, f"Нет доступа к диску (код: {response.status})"
            
            test_folder = f"/test_folder_{int(datetime.now().timestamp())}"
            create_url = f"{self.base_url}/resources"
            params = {"path": test_folder}
            
            async with aiohttp.ClientSession() as session:
                async with session.put(create_url, headers=self.headers, params=params) as response:
                    if response.status in [200, 201, 202]:
                        async with session.delete(create_url, headers=self.headers, params=params) as del_response:
                            return True, "Есть права на запись!"
                    else:
                        return False, f"Нет прав на запись (код: {response.status})"
        except Exception as e:
            return False, str(e)
    
    def create_folder(self, folder_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path}
            response = requests.put(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 201, 202]
        except:
            return False


# Состояния FSM
class AuthStates(StatesGroup):
    waiting_for_yandex_code = State()


class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_every_day_time = State()
    waiting_for_edit_text = State()


class SettingsStates(StatesGroup):
    waiting_for_max_backups = State()
    waiting_for_check_time = State()
    waiting_for_timezone = State()
    waiting_for_upload_backup = State()


# Функции для работы с данными
def init_folders():
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f:
            json.dump({}, f)
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            'backup_path': '/MyUved_backups',
            'max_backups': 5,
            'daily_check_time': '06:00',
            'notifications_enabled': True,
            'timezone': 'Europe/Moscow'
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f)


def load_data():
    global notifications, config, user_tokens, notifications_enabled
    with open(DATA_FILE, 'r') as f:
        notifications = json.load(f)
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
        notifications_enabled = config.get('notifications_enabled', True)
    
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                token_data = json.load(f)
                user_tokens = {int(k): v for k, v in token_data.items()}
                logger.info(f"Загружены токены для пользователей: {list(user_tokens.keys())}")
        except Exception as e:
            logger.error(f"Ошибка загрузки токенов: {e}")
            user_tokens = {}


def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(notifications, f, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def save_user_token(user_id: int, token: str):
    global user_tokens
    user_tokens[user_id] = token
    with open(TOKEN_FILE, 'w') as f:
        json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)
    logger.info(f"✅ Токен сохранен для user {user_id}")


def get_user_token(user_id: int) -> Optional[str]:
    token = user_tokens.get(user_id)
    if token:
        logger.info(f"Токен найден для user {user_id}")
    else:
        logger.info(f"Токен НЕ найден для user {user_id}")
    return token


def delete_user_token(user_id: int):
    if user_id in user_tokens:
        del user_tokens[user_id]
        with open(TOKEN_FILE, 'w') as f:
            json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)
        logger.info(f"Токен удален для user {user_id}")


async def check_yandex_access(user_id: int) -> tuple:
    token = get_user_token(user_id)
    if not token:
        return False, "Нет токена авторизации"
    
    yandex_disk = YandexDiskAPI(token)
    access, message = await yandex_disk.check_access()
    return access, message


async def create_backup(user_id: int = None) -> tuple:
    try:
        timestamp = get_current_time().strftime('%Y%m%d_%H%M%S')
        backup_file = Path(BACKUP_DIR) / f'backup_{timestamp}.json'
        
        backup_data = {
            'notifications': notifications,
            'config': config,
            'timestamp': timestamp,
            'version': BOT_VERSION
        }
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        backup_created = False
        backup_location = None
        
        if user_id:
            token = get_user_token(user_id)
            if token:
                yandex_disk = YandexDiskAPI(token)
                access, _ = await check_yandex_access(user_id)
                if access:
                    remote_path = f"{config['backup_path']}/backup_{timestamp}.json"
                    yandex_disk.create_folder(config['backup_path'])
                    
                    if yandex_disk.upload_file(str(backup_file), remote_path):
                        backup_created = True
                        backup_location = "Яндекс.Диск"
        
        return backup_created, backup_file, backup_location
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
        return False, None, None


async def show_backup_notification(message: types.Message):
    if ADMIN_ID:
        success, _, location = await create_backup(ADMIN_ID)
        if success:
            msg = await message.reply(f"✅ **Бэкап создан** ({location})", parse_mode='Markdown')
            await asyncio.sleep(5)
            await msg.delete()


def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("➕ Добавить уведомление"),
        KeyboardButton("📋 Список уведомлений"),
        KeyboardButton("⚙️ Настройки")
    )
    return keyboard


# ========== ОБРАБОТЧИКИ ==========

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} запустил бота")
    await state.finish()
    
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        await message.reply("❌ У вас нет доступа к этому боту")
        return
    
    user_id = message.from_user.id
    token = get_user_token(user_id)
    
    if token:
        access, access_message = await check_yandex_access(user_id)
        if access:
            await message.reply(
                f"✅ **Доступ к Яндекс.Диску имеется!**\n\n"
                f"🤖 **Версия бота:** v{BOT_VERSION}\n"
                f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}",
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup(row_width=1)
            keyboard.add(InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth"))
            
            await message.reply(
                f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
                f"Причина: {access_message}\n\n"
                f"Нажмите кнопку для авторизации:",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        keyboard = InlineKeyboardMarkup(row_width=1)
        keyboard.add(InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth"))
        
        await message.reply(
            f"👋 **Добро пожаловать!**\n\n"
            f"🤖 **Версия бота:** v{BOT_VERSION}\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов необходимо авторизоваться.\n\n"
            f"Нажмите кнопку ниже для авторизации:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await message.reply("👋 **Выберите действие:**", reply_markup=get_main_keyboard(), parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "start_auth")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback.from_user.id} начал авторизацию")
    
    # Очищаем старый токен
    delete_user_token(callback.from_user.id)
    
    auth_url = get_auth_url()
    
    await bot.send_message(
        callback.from_user.id,
        f"🔑 **Авторизация через Яндекс**\n\n"
        f"1️⃣ Перейдите по ссылке:\n"
        f"🔗 [Нажмите для авторизации]({auth_url})\n\n"
        f"2️⃣ Войдите в аккаунт Яндекс\n"
        f"3️⃣ Разрешите доступ\n"
        f"4️⃣ Скопируйте **код** из адресной строки (после `code=`)\n"
        f"5️⃣ **Отправьте код сюда текстовым сообщением**\n\n"
        f"📝 Пример кода: `5j4iyexor5ltn4ym`\n\n"
        f"⏰ **У вас есть 5 минут**",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()


@dp.message_handler(state=AuthStates.waiting_for_yandex_code)
async def receive_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    logger.info(f"Получен код от user {user_id}, длина: {len(code)}")
    
    if not code or len(code) < 10:
        await message.reply("❌ **Ошибка!** Код слишком короткий. Попробуйте снова.", parse_mode='Markdown')
        return
    
    status_msg = await message.reply("⏳ **Получение токена...**", parse_mode='Markdown')
    
    token = await get_access_token(code)
    
    if token:
        save_user_token(user_id, token)
        await status_msg.edit_text("⏳ **Проверка доступа...**")
        
        access, access_message = await check_yandex_access(user_id)
        
        if access:
            await status_msg.delete()
            await message.reply(
                f"✅ **Авторизация успешна!**\n\n"
                f"Доступ к Яндекс.Диску получен.\n\n"
                f"Теперь вы можете создавать уведомления и бэкапы.",
                parse_mode='Markdown'
            )
            await cmd_start(message, state)
        else:
            delete_user_token(user_id)
            await status_msg.edit_text(
                f"❌ **Токен получен, но нет доступа к Диску!**\n\n"
                f"Причина: {access_message}\n\n"
                f"Попробуйте авторизоваться снова.",
                parse_mode='Markdown'
            )
    else:
        await status_msg.edit_text(
            f"❌ **Ошибка авторизации!**\n\n"
            f"Не удалось получить токен. Проверьте код и попробуйте снова.",
            parse_mode='Markdown'
        )
    
    await state.finish()


@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление", state='*')
async def add_notification(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply("✏️ **Введите текст уведомления:**\n\n💡 Для отмены отправьте /cancel", parse_mode='Markdown')
    await NotificationStates.waiting_for_text.set()


@dp.message_handler(state=NotificationStates.waiting_for_text)
async def get_text(message: types.Message, state: FSMContext):
    if not message.text:
        await message.reply("❌ Введите текст", parse_mode='Markdown')
        return
    
    await state.update_data(text=message.text)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("📅 Каждый день", callback_data="time_every_day")
    )
    
    await message.reply("⏱️ **Когда уведомить?**", reply_markup=keyboard, parse_mode='Markdown')
    await NotificationStates.waiting_for_time_type.set()


@dp.callback_query_handler(lambda c: c.data.startswith('time_'), state=NotificationStates.waiting_for_time_type)
async def choose_time_type(callback: types.CallbackQuery, state: FSMContext):
    time_type = callback.data.replace('time_', '')
    await state.update_data(time_type=time_type)
    
    if time_type == 'hours':
        await callback.message.reply("⌛ **Введите количество часов:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_hours.set()
    elif time_type == 'days':
        await callback.message.reply("📅 **Введите количество дней:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_days.set()
    elif time_type == 'months':
        await callback.message.reply("📆 **Введите количество месяцев:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_months.set()
    elif time_type == 'specific':
        await callback.message.reply("🗓️ **Введите дату**\n\nФормат: `ДД.ММ.ГГГГ ЧЧ:ММ` или `ДД.ММ ЧЧ:ММ`", parse_mode='Markdown')
        await NotificationStates.waiting_for_specific_date.set()
    elif time_type == 'every_day':
        await callback.message.reply("⏰ **Введите время**\n\nФормат: `ЧЧ:ММ`", parse_mode='Markdown')
        await NotificationStates.waiting_for_every_day_time.set()
    
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            await message.reply("❌ Введите положительное число", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notify_time = get_current_time() + timedelta(hours=hours)
        
        next_num = len(notifications) + 1
        notif_id = str(next_num)
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': get_current_time().isoformat(),
            'notified': False,
            'num': next_num
        }
        save_data()
        
        await message.reply(f"✅ **Уведомление #{next_num} создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ Введите число", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_days)
async def set_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0:
            await message.reply("❌ Введите положительное число", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notify_time = get_current_time() + timedelta(days=days)
        
        next_num = len(notifications) + 1
        notif_id = str(next_num)
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': get_current_time().isoformat(),
            'notified': False,
            'num': next_num
        }
        save_data()
        
        await message.reply(f"✅ **Уведомление #{next_num} создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ Введите число", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_months)
async def set_months(message: types.Message, state: FSMContext):
    try:
        months = int(message.text)
        if months <= 0:
            await message.reply("❌ Введите положительное число", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notify_time = get_current_time() + timedelta(days=months * 30)
        
        next_num = len(notifications) + 1
        notif_id = str(next_num)
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': get_current_time().isoformat(),
            'notified': False,
            'num': next_num
        }
        save_data()
        
        await message.reply(f"✅ **Уведомление #{next_num} создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ Введите число", parse_mode='Markdown')


def parse_date(date_str: str, current_time: datetime) -> Optional[datetime]:
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    # Формат ДД.ММ.ГГГГ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        day, month, year, hour, minute = map(int, match.groups())
        try:
            return tz.localize(datetime(year, month, day, hour, minute))
        except:
            return None
    
    # Формат ДД.ММ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        day, month, hour, minute = map(int, match.groups())
        year = current_time.year
        try:
            result = tz.localize(datetime(year, month, day, hour, minute))
            if result < current_time:
                result = tz.localize(datetime(year + 1, month, day, hour, minute))
            return result
        except:
            return None
    
    return None


@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def set_specific_date(message: types.Message, state: FSMContext):
    current_time = get_current_time()
    notify_time = parse_date(message.text, current_time)
    
    if notify_time is None:
        await message.reply("❌ **Неверный формат даты!**\n\nИспользуйте:\n`31.12.2025 23:59` или `06.04 9:00`", parse_mode='Markdown')
        return
    
    if notify_time <= current_time:
        await message.reply("❌ **Дата должна быть в будущем!**", parse_mode='Markdown')
        return
    
    data = await state.get_data()
    next_num = len(notifications) + 1
    notif_id = str(next_num)
    
    notifications[notif_id] = {
        'text': data['text'],
        'time': notify_time.isoformat(),
        'created': current_time.isoformat(),
        'notified': False,
        'num': next_num
    }
    save_data()
    
    await message.reply(f"✅ **Уведомление #{next_num} создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
    await show_backup_notification(message)
    await state.finish()


@dp.message_handler(state=NotificationStates.waiting_for_every_day_time)
async def set_every_day_time(message: types.Message, state: FSMContext):
    match = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
    if not match:
        await message.reply("❌ **Неверный формат!** Используйте `ЧЧ:ММ`", parse_mode='Markdown')
        return
    
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        await message.reply("❌ **Некорректное время!**", parse_mode='Markdown')
        return
    
    data = await state.get_data()
    next_num = len(notifications) + 1
    notif_id = str(next_num)
    
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    first_time = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
    
    if first_time <= now:
        first_time += timedelta(days=1)
    
    notifications[notif_id] = {
        'text': data['text'],
        'time': first_time.isoformat(),
        'created': now.isoformat(),
        'notified': False,
        'num': next_num,
        'repeat_type': 'every_day',
        'repeat_hour': hour,
        'repeat_minute': minute
    }
    save_data()
    
    await message.reply(f"✅ **Ежедневное уведомление #{next_num} создано!**\n📝 {data['text']}\n⏰ Время: {hour:02d}:{minute:02d}", parse_mode='Markdown')
    await show_backup_notification(message)
    await state.finish()


@dp.message_handler(lambda m: m.text == "📋 Список уведомлений", state='*')
async def list_notifications(message: types.Message, state: FSMContext):
    await state.finish()
    
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    for notif_id, notif in notifications.items():
        if notif.get('repeat_type') == 'every_day':
            hour = notif.get('repeat_hour', 0)
            minute = notif.get('repeat_minute', 0)
            text = f"🔄 **Уведомление #{notif.get('num', notif_id)}**\n📝 {notif['text']}\n🔄 Ежедневно в {hour:02d}:{minute:02d}"
        else:
            notify_time = datetime.fromisoformat(notif['time'])
            if notify_time.tzinfo is None:
                notify_time = tz.localize(notify_time)
            text = f"📅 **Уведомление #{notif.get('num', notif_id)}**\n📝 {notif['text']}\n⏰ {notify_time.strftime('%d.%m.%Y в %H:%M')}"
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
        )
        
        await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
    
    await message.reply(f"📊 **Всего уведомлений:** {len(notifications)}", parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data.startswith('delete_'))
async def delete_notification(callback: types.CallbackQuery):
    notif_id = callback.data.replace('delete_', '')
    
    if notif_id in notifications:
        notif_num = notifications[notif_id].get('num', notif_id)
        del notifications[notif_id]
        
        # Перенумеровываем
        new_notifications = {}
        for i, (nid, notif) in enumerate(notifications.items(), 1):
            notif['num'] = i
            new_notifications[str(i)] = notif
        notifications.clear()
        notifications.update(new_notifications)
        save_data()
        
        await bot.send_message(callback.from_user.id, f"✅ **Уведомление #{notif_num} удалено**", parse_mode='Markdown')
        await show_backup_notification(callback.message)
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('edit_'))
async def edit_notification(callback: types.CallbackQuery, state: FSMContext):
    notif_id = callback.data.replace('edit_', '')
    
    if notif_id not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    
    await state.update_data(edit_id=notif_id)
    await bot.send_message(
        callback.from_user.id,
        f"✏️ **Введите новый текст для уведомления #{notifications[notif_id].get('num', notif_id)}:**\n\nСтарый текст: {notifications[notif_id]['text']}",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_edit_text.set()
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_edit_text)
async def save_edited_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    edit_id = data.get('edit_id')
    
    if not edit_id or edit_id not in notifications:
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
        await state.finish()
        return
    
    notifications[edit_id]['text'] = message.text
    save_data()
    
    await message.reply(f"✅ **Текст изменен!**\nНовый текст: {message.text}", parse_mode='Markdown')
    await show_backup_notification(message)
    await state.finish()


@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def settings_menu(message: types.Message, state: FSMContext):
    await state.finish()
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔢 Максимум бэкапов", callback_data="set_max_backups"),
        InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time"),
        InlineKeyboardButton("🌍 Часовой пояс", callback_data="set_timezone"),
        InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth"),
        InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="info")
    )
    
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=keyboard, parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "set_max_backups")
async def set_max_backups(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"📊 **Текущее количество:** {config.get('max_backups', 5)}\n\nВведите число (1-20):",
        parse_mode='Markdown'
    )
    await SettingsStates.waiting_for_max_backups.set()
    await callback.answer()


@dp.message_handler(state=SettingsStates.waiting_for_max_backups)
async def save_max_backups(message: types.Message, state: FSMContext):
    try:
        max_backups = int(message.text)
        if 1 <= max_backups <= 20:
            config['max_backups'] = max_backups
            save_data()
            await message.reply(f"✅ **Установлено:** {max_backups}", parse_mode='Markdown')
        else:
            await message.reply("❌ **Число от 1 до 20**", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Введите число**", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "set_check_time")
async def set_check_time(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"🕐 **Текущее время:** {config.get('daily_check_time', '06:00')}\n\nВведите время (ЧЧ:ММ):",
        parse_mode='Markdown'
    )
    await SettingsStates.waiting_for_check_time.set()
    await callback.answer()


@dp.message_handler(state=SettingsStates.waiting_for_check_time)
async def save_check_time(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%H:%M")
        config['daily_check_time'] = message.text
        save_data()
        await message.reply(f"✅ **Время установлено:** {message.text}", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Формат ЧЧ:ММ**", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "set_timezone")
async def set_timezone(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    for name in TIMEZONES.keys():
        keyboard.add(InlineKeyboardButton(name, callback_data=f"tz_{name}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_tz"))
    
    await bot.send_message(
        callback.from_user.id,
        f"🌍 **Выберите часовой пояс**\n\nТекущий: {config.get('timezone', 'Europe/Moscow')}",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("tz_"))
async def save_timezone(callback: types.CallbackQuery, state: FSMContext):
    tz_name = callback.data.replace("tz_", "")
    tz_value = TIMEZONES.get(tz_name, 'Europe/Moscow')
    config['timezone'] = tz_value
    save_data()
    
    await bot.send_message(
        callback.from_user.id,
        f"✅ **Часовой пояс:** {tz_name}\n🕐 {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}",
        parse_mode='Markdown'
    )
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_tz")
async def cancel_tz(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "create_backup")
async def manual_backup(callback: types.CallbackQuery):
    status_msg = await bot.send_message(callback.from_user.id, "⏳ **Создание бэкапа...**", parse_mode='Markdown')
    success, _, location = await create_backup(ADMIN_ID)
    
    if success:
        await status_msg.edit_text(f"✅ **Бэкап создан** ({location})", parse_mode='Markdown')
        await asyncio.sleep(3)
        await status_msg.delete()
    else:
        await status_msg.edit_text("❌ **Ошибка создания бэкапа!**", parse_mode='Markdown')
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    token = get_user_token(user_id)
    
    disk_access = "❌ Нет"
    if token:
        access, _ = await check_yandex_access(user_id)
        disk_access = "✅ Да" if access else "❌ Нет"
    
    info = f"""
📊 **СТАТИСТИКА**

🤖 **Версия:** v{BOT_VERSION}

📝 **Уведомлений:** `{len(notifications)}`
💾 **Максимум бэкапов:** `{config.get('max_backups', 5)}`
🕐 **Проверка:** `{config.get('daily_check_time', '06:00')}`
🌍 **Часовой пояс:** `{config.get('timezone', 'Europe/Moscow')}`
🕐 **Текущее время:** `{get_current_time().strftime('%d.%m.%Y %H:%M:%S')}`

🔑 **Токен:** `{'✅ Есть' if token else '❌ Нет'}`
💾 **Яндекс.Диск:** `{disk_access}`
"""
    await bot.send_message(callback.from_user.id, info, parse_mode='Markdown')
    await callback.answer()


@dp.message_handler(commands=['cancel'], state='*')
async def cancel_operation(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.reply("❌ **Нет активных операций**", parse_mode='Markdown')
        return
    
    await state.finish()
    await message.reply("✅ **Операция отменена!**", parse_mode='Markdown')


@dp.message_handler(commands=['version'])
async def show_version(message: types.Message):
    await message.reply(
        f"🤖 **Бот для уведомлений**\n📌 **Версия:** v{BOT_VERSION}\n📅 **Дата:** {BOT_VERSION_DATE}",
        parse_mode='Markdown'
    )


async def check_notifications():
    while True:
        if notifications_enabled:
            now = get_current_time()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            
            for notif_id, notif in list(notifications.items()):
                if notif.get('notified', False):
                    continue
                
                repeat_type = notif.get('repeat_type')
                
                if repeat_type == 'every_day':
                    hour = notif.get('repeat_hour', 0)
                    minute = notif.get('repeat_minute', 0)
                    today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
                    
                    last_trigger = notif.get('last_trigger')
                    if last_trigger:
                        last = datetime.fromisoformat(last_trigger)
                        if last.date() == now.date():
                            continue
                    
                    if now >= today_trigger:
                        await bot.send_message(
                            ADMIN_ID,
                            f"🔔 **ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n⏰ {today_trigger.strftime('%d.%m.%Y %H:%M')}",
                            parse_mode='Markdown'
                        )
                        notif['last_trigger'] = now.isoformat()
                        save_data()
                
                else:
                    notify_time = datetime.fromisoformat(notif['time'])
                    if notify_time.tzinfo is None:
                        notify_time = tz.localize(notify_time)
                    
                    if now >= notify_time and not notif.get('notified', False):
                        await bot.send_message(
                            ADMIN_ID,
                            f"🔔 **НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n⏰ {notify_time.strftime('%d.%m.%Y %H:%M:%S')}",
                            parse_mode='Markdown'
                        )
                        notif['notified'] = True
                        save_data()
        
        await asyncio.sleep(30)


async def on_startup(dp):
    init_folders()
    load_data()
    
    logger.info(f"\n{'='*50}")
    logger.info(f"🤖 БОТ ДЛЯ УВЕДОМЛЕНИЙ v{BOT_VERSION}")
    logger.info(f"{'='*50}")
    
    if ADMIN_ID:
        token = get_user_token(ADMIN_ID)
        if token:
            logger.info(f"✅ Токен найден для ADMIN_ID")
            access, message = await check_yandex_access(ADMIN_ID)
            if access:
                logger.info("✅ Доступ к Яндекс.Диску получен")
            else:
                logger.warning(f"⚠️ Доступ к Диску: {message}")
        else:
            logger.warning("❌ Нет токена для ADMIN_ID")
    
    logger.info(f"📝 Уведомлений: {len(notifications)}")
    logger.info(f"🌍 Часовой пояс: {config.get('timezone', 'Europe/Moscow')}")
    logger.info(f"{'='*50}\n")
    
    asyncio.create_task(check_notifications())
    logger.info("✅ Бот успешно запущен!")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)