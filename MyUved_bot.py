import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode
from io import BytesIO
import pytz
import re

import aiohttp
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, InputFile
from aiogram.utils import executor
from dotenv import load_dotenv

# Версия бота
BOT_VERSION = "2.1"
BOT_VERSION_DATE = "05.04.2026"

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

# Проверка наличия всех необходимых переменных
if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET]):
    print("❌ Ошибка: Не все переменные окружения заданы!")
    exit(1)

# Инициализация бота с MemoryStorage для FSM
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

# URL для Яндекс.Диск API
YANDEX_API_BASE = "https://cloud-api.yandex.net/v1/disk"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"

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

# Дни недели
WEEKDAYS = {
    'monday': 'Понедельник',
    'tuesday': 'Вторник',
    'wednesday': 'Среда',
    'thursday': 'Четверг',
    'friday': 'Пятница',
    'saturday': 'Суббота',
    'sunday': 'Воскресенье'
}


def get_current_time():
    """Возвращает текущее время с учетом часового пояса"""
    timezone_str = config.get('timezone', 'Europe/Moscow')
    tz = pytz.timezone(timezone_str)
    return datetime.now(tz)


def parse_date(date_str: str) -> Optional[datetime]:
    """Парсит дату из различных форматов"""
    now = get_current_time()
    current_year = now.year
    current_month = now.month
    
    date_str = date_str.strip()
    
    # Формат: 31.12.2025 или 31.12.25
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', date_str)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day, 0, 0)
        except ValueError:
            return None
    
    # Формат: 31.12
    match = re.match(r'^(\d{1,2})\.(\d{1,2})$', date_str)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = current_year
        if month < current_month or (month == current_month and day < now.day):
            year += 1
        try:
            return datetime(year, month, day, 0, 0)
        except ValueError:
            return None
    
    # Формат: 31
    match = re.match(r'^(\d{1,2})$', date_str)
    if match:
        day = int(match.group(1))
        month = current_month
        year = current_year
        if day < now.day:
            month += 1
            if month > 12:
                month = 1
                year += 1
        try:
            return datetime(year, month, day, 0, 0)
        except ValueError:
            return None
    
    # Формат: 2025-12-31 23:59 (ISO)
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    
    return None


def parse_time(time_str: str) -> Optional[datetime.time]:
    """Парсит время из формата ЧЧ:ММ"""
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return None


def get_next_weekday(target_weekday: int, time: datetime.time = None) -> datetime:
    """Возвращает следующую дату для указанного дня недели"""
    now = get_current_time()
    days_ahead = target_weekday - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    next_date = now + timedelta(days=days_ahead)
    if time:
        next_date = next_date.replace(hour=time.hour, minute=time.minute, second=0, microsecond=0)
    return next_date


def get_auth_url() -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI
    }
    return f"{YANDEX_OAUTH_URL}?{urlencode(params)}"


def get_token_url() -> str:
    params = {
        "response_type": "token",
        "client_id": CLIENT_ID
    }
    return f"{YANDEX_OAUTH_URL}?{urlencode(params)}"


async def get_access_token(auth_code: str) -> Optional[str]:
    url = "https://oauth.yandex.ru/token"
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("access_token")
                else:
                    error_text = await response.text()
                    print(f"Ошибка получения токена: {response.status} - {error_text}")
                    return None
        except Exception as e:
            print(f"Исключение при получении токена: {e}")
            return None


class YandexDiskAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = YANDEX_API_BASE
        self.headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }
    
    async def check_access_async(self):
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
                            return True, "Есть права на запись! Тестовая папка создана и удалена."
                    else:
                        return False, f"Нет прав на запись (код: {response.status})"
        except Exception as e:
            print(f"Ошибка проверки доступа: {e}")
            return False, str(e)
    
    def check_access(self):
        try:
            url = f"{self.base_url}/"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                return False, "Нет доступа к диску"
            
            test_folder = f"/test_folder_{int(datetime.now().timestamp())}"
            create_url = f"{self.base_url}/resources"
            params = {"path": test_folder}
            create_response = requests.put(create_url, headers=self.headers, params=params, timeout=10)
            
            if create_response.status_code in [200, 201, 202]:
                delete_response = requests.delete(create_url, headers=self.headers, params=params, timeout=10)
                return True, "Есть права на запись"
            else:
                return False, "Нет прав на запись"
        except Exception as e:
            print(f"Ошибка проверки доступа: {e}")
            return False, str(e)
    
    async def upload_file_content(self, remote_path: str, content: str) -> bool:
        try:
            url = f"{self.base_url}/resources/upload"
            params = {"path": remote_path, "overwrite": "true"}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as response:
                    if response.status != 200:
                        return False
                    data = await response.json()
                    upload_url = data.get("href")
                    
                    if not upload_url:
                        return False
                    
                    async with session.put(upload_url, data=content.encode('utf-8'), headers={"Content-Type": "text/plain"}) as upload_response:
                        return upload_response.status in [200, 201]
        except Exception as e:
            print(f"Ошибка загрузки файла: {e}")
            return False
    
    async def check_file_exists(self, remote_path: str) -> bool:
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as response:
                    return response.status == 200
        except Exception as e:
            print(f"Ошибка проверки файла: {e}")
            return False
    
    async def delete_file_async(self, remote_path: str) -> bool:
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path, "permanently": "true"}
            
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=self.headers, params=params) as response:
                    return response.status in [200, 202, 204]
        except Exception as e:
            print(f"Ошибка удаления: {e}")
            return False
    
    def create_folder(self, folder_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path}
            response = requests.put(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 201, 202]
        except Exception as e:
            print(f"Ошибка создания папки: {e}")
            return False
    
    def upload_file(self, local_path, remote_path):
        try:
            url = f"{self.base_url}/resources/upload"
            params = {"path": remote_path, "overwrite": "true"}
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if response.status_code == 200:
                upload_url = response.json().get("href")
                with open(local_path, 'rb') as f:
                    upload_response = requests.put(upload_url, files={"file": f}, timeout=30)
                    return upload_response.status_code == 201
            return False
        except Exception as e:
            print(f"Ошибка загрузки файла: {e}")
            return False
    
    def list_files(self, folder_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path, "limit": 100}
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if response.status_code == 200:
                items = response.json().get("_embedded", {}).get("items", [])
                return [item for item in items if item.get("type") == "file"]
            return []
        except Exception as e:
            print(f"Ошибка получения списка файлов: {e}")
            return []
    
    def delete_file(self, remote_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path, "permanently": "true"}
            response = requests.delete(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 202, 204]
        except Exception as e:
            print(f"Ошибка удаления файла: {e}")
            return False
    
    def download_file(self, remote_path, local_path):
        try:
            url = f"{self.base_url}/resources/download"
            params = {"path": remote_path}
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if response.status_code == 200:
                download_url = response.json().get("href")
                download_response = requests.get(download_url, timeout=30)
                
                with open(local_path, 'wb') as f:
                    f.write(download_response.content)
                return True
            return False
        except Exception as e:
            print(f"Ошибка скачивания файла: {e}")
            return False


# Состояния FSM
class AuthStates(StatesGroup):
    waiting_for_yandex_code = State()
    waiting_for_direct_token = State()


class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time_type = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_specific_time = State()
    waiting_for_weekdays = State()
    waiting_for_weekday_time = State()
    waiting_for_edit_notification = State()
    waiting_for_edit_text = State()
    waiting_for_edit_time = State()


class SettingsStates(StatesGroup):
    waiting_for_backup_path = State()
    waiting_for_max_backups = State()
    waiting_for_check_time = State()
    waiting_for_upload_backup = State()
    waiting_for_timezone = State()
    waiting_for_restore_source = State()
    waiting_for_backup_selection = State()


# Инициализация папок
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
        except:
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


def get_user_token(user_id: int) -> Optional[str]:
    return user_tokens.get(user_id)


def delete_user_token(user_id: int):
    if user_id in user_tokens:
        del user_tokens[user_id]
        with open(TOKEN_FILE, 'w') as f:
            json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)


async def check_yandex_access_with_test(user_id: int) -> tuple:
    token = get_user_token(user_id)
    
    if not token:
        return False, "❌ Нет токена авторизации", None
    
    try:
        yandex_disk = YandexDiskAPI(token)
        access, message = await yandex_disk.check_access_async()
        
        if access:
            yandex_disk.create_folder(config['backup_path'])
            print(f"✅ Доступ к Яндекс.Диску для user {user_id} успешно получен")
            return True, f"✅ {message}", yandex_disk
        else:
            print(f"❌ Нет доступа к Яндекс.Диску для user {user_id}: {message}")
            return False, f"❌ {message}", None
            
    except Exception as e:
        print(f"Ошибка доступа к Яндекс.Диску: {e}")
        return False, f"❌ Ошибка: {str(e)}", None


async def check_yandex_access(user_id: int) -> tuple:
    result, message, _ = await check_yandex_access_with_test(user_id)
    return result, message


async def send_backup_to_telegram(backup_file: Path) -> bool:
    try:
        with open(backup_file, 'rb') as f:
            await bot.send_document(
                ADMIN_ID,
                InputFile(BytesIO(f.read()), filename=backup_file.name),
                caption=f"📦 **Бэкап от {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n\n💡 Сохраните этот файл для восстановления",
                parse_mode='Markdown'
            )
        return True
    except Exception as e:
        print(f"Ошибка отправки бэкапа в Telegram: {e}")
        return False


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
                        await cleanup_old_backups(user_id)
                        print(f"✅ Бэкап создан на Яндекс.Диске")
                        backup_created = True
                        backup_location = "Яндекс.Диск"
        
        if not backup_created:
            if await send_backup_to_telegram(backup_file):
                backup_created = True
                backup_location = "Telegram"
        
        return backup_created, backup_file, backup_location
    except Exception as e:
        print(f"Ошибка создания бэкапа: {e}")
        return False, None, None


async def cleanup_old_backups(user_id: int = None):
    try:
        if user_id:
            token = get_user_token(user_id)
            if token:
                yandex_disk = YandexDiskAPI(token)
                access, _ = await check_yandex_access(user_id)
                if access:
                    files = yandex_disk.list_files(config['backup_path'])
                    backup_files = [f for f in files if f['name'].startswith('backup_')]
                    backup_files.sort(key=lambda x: x['name'], reverse=True)
                    max_backups = config.get('max_backups', 5)
                    
                    for old_file in backup_files[max_backups:]:
                        remote_path = f"{config['backup_path']}/{old_file['name']}"
                        yandex_disk.delete_file(remote_path)
        
        local_backups = sorted(Path(BACKUP_DIR).glob('backup_*.json'))
        max_backups = config.get('max_backups', 5)
        for old_backup in local_backups[:-max_backups]:
            old_backup.unlink()
    except Exception as e:
        print(f"Ошибка очистки старых бэкапов: {e}")


async def restore_from_yadisk_backup(backup_name: str, user_id: int) -> bool:
    global notifications, config
    
    try:
        token = get_user_token(user_id)
        if not token:
            return False
        
        yandex_disk = YandexDiskAPI(token)
        remote_path = f"{config['backup_path']}/{backup_name}"
        local_path = Path(BACKUP_DIR) / f"restore_{backup_name}"
        
        if yandex_disk.download_file(remote_path, str(local_path)):
            with open(local_path, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            if 'notifications' in backup_data:
                notifications = backup_data['notifications']
                if 'config' in backup_data:
                    config = backup_data['config']
                save_data()
                return True
        
        return False
    except Exception as e:
        print(f"Ошибка восстановления из бэкапа Яндекс.Диска: {e}")
        return False


async def get_yadisk_backups(user_id: int) -> List[Dict]:
    try:
        token = get_user_token(user_id)
        if not token:
            return []
        
        yandex_disk = YandexDiskAPI(token)
        files = yandex_disk.list_files(config['backup_path'])
        backups = [f for f in files if f['name'].startswith('backup_') and f['name'].endswith('.json')]
        backups.sort(key=lambda x: x['name'], reverse=True)
        return backups
    except Exception as e:
        print(f"Ошибка получения списка бэкапов: {e}")
        return []


async def check_notifications():
    global notifications_enabled
    while True:
        if notifications_enabled:
            now = get_current_time()
            
            for notif_id, notif in list(notifications.items()):
                notify_time = datetime.fromisoformat(notif['time'])
                if notify_time.tzinfo is None:
                    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                    notify_time = tz.localize(notify_time)
                
                if now >= notify_time and not notif.get('notified', False):
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton("✅ Удалить", callback_data=f"delete_{notif_id}"),
                        InlineKeyboardButton("⏰ Отложить на час", callback_data=f"snooze_{notif_id}_1")
                    )
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔔 НАПОМИНАНИЕ!\n\n📝 {notif['text']}\n\n⏰ {notify_time.strftime('%d.%m.%Y %H:%M:%S')}",
                        reply_markup=keyboard
                    )
                    
                    notifications[notif_id]['notified'] = True
                    save_data()
                    
        await asyncio.sleep(30)


checking_daily = False
async def daily_check():
    global checking_daily
    while True:
        now = get_current_time()
        check_time = config.get('daily_check_time', '06:00')
        check_hour, check_minute = map(int, check_time.split(':'))
        target_time = now.replace(hour=check_hour, minute=check_minute, second=0, microsecond=0)
        
        if now >= target_time and not checking_daily:
            checking_daily = True
            
            if ADMIN_ID:
                access, message = await check_yandex_access(ADMIN_ID)
                if not access:
                    print(f"❌ Ежедневная проверка: нет доступа - {message}")
                else:
                    print("✅ Ежедневная проверка: доступ есть")
            
            await asyncio.sleep(60)
            checking_daily = False
        
        await asyncio.sleep(30)


def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("➕ Добавить уведомление"),
        KeyboardButton("📋 Список уведомлений")
    )
    keyboard.add(
        KeyboardButton("⚙️ Настройки"),
        KeyboardButton("💾 Создать бэкап")
    )
    return keyboard


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message, state: FSMContext):
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
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n"
                f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}",
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup(row_width=2)
            auth_url = get_auth_url()
            token_url = get_token_url()
            keyboard.add(InlineKeyboardButton("🔑 Авторизация (с кодом)", url=auth_url))
            keyboard.add(InlineKeyboardButton("🔓 Получить токен напрямую", url=token_url))
            keyboard.add(InlineKeyboardButton("✅ Я получил код", callback_data="enter_code"))
            keyboard.add(InlineKeyboardButton("📝 Ввести токен вручную", callback_data="enter_direct_token"))
            
            await message.reply(
                f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
                f"Причина: {access_message}\n\n"
                f"**Варианты авторизации:**\n"
                f"1️⃣ Авторизация с кодом\n"
                f"2️⃣ Получить токен напрямую\n\n"
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        keyboard = InlineKeyboardMarkup(row_width=2)
        auth_url = get_auth_url()
        token_url = get_token_url()
        keyboard.add(InlineKeyboardButton("🔑 Авторизация (с кодом)", url=auth_url))
        keyboard.add(InlineKeyboardButton("🔓 Получить токен напрямую", url=token_url))
        keyboard.add(InlineKeyboardButton("✅ Я получил код", callback_data="enter_code"))
        keyboard.add(InlineKeyboardButton("📝 Ввести токен вручную", callback_data="enter_direct_token"))
        
        await message.reply(
            f"👋 **Добро пожаловать!**\n\n"
            f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов необходимо авторизоваться.\n\n"
            f"**Как авторизоваться:**\n"
            f"1️⃣ Нажмите на кнопку ниже\n"
            f"2️⃣ Войдите в аккаунт Яндекс\n"
            f"3️⃣ Разрешите доступ\n"
            f"4️⃣ Скопируйте код/токен\n"
            f"5️⃣ Отправьте его боту",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await message.reply(
        "👋 **Выберите действие:**",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )


@dp.callback_query_handler(lambda c: c.data == "enter_code")
async def ask_for_code(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"🔑 **Введите код авторизации**\n\n"
        f"Отправьте код, который вы получили после авторизации:\n"
        f"📝 Пример: `5j4iyexor5ltn4ym`\n\n"
        f"💡 **Важно:** Код нужно ввести текстовым сообщением!\n"
        f"⏰ **У вас есть 3 минуты** на ввод кода",
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "enter_direct_token")
async def ask_for_direct_token(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"🔓 **Введите токен доступа напрямую**\n\n"
        f"Отправьте токен, который вы получили:\n"
        f"📝 Пример: `y0_AgAAAAABX...`\n\n"
        f"💡 **Как получить токен:**\n"
        f"1. Нажмите «Получить токен напрямую»\n"
        f"2. Разрешите доступ\n"
        f"3. Скопируйте токен из адресной строки\n"
        f"4. Вставьте его сюда\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод токена",
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_direct_token.set()
    await callback.answer()


@dp.message_handler(state=AuthStates.waiting_for_direct_token)
async def receive_direct_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    user_id = message.from_user.id
    
    if not token or len(token) < 20:
        await message.reply("❌ **Ошибка!** Токен слишком короткий.", parse_mode='Markdown')
        return
    
    status_msg = await message.reply("⏳ **Проверка токена...**", parse_mode='Markdown')
    
    save_user_token(user_id, token)
    access, access_message, yandex_disk = await check_yandex_access_with_test(user_id)
    
    if access:
        yandex_disk.create_folder(config['backup_path'])
        result_message = (
            f"✅ **Токен действителен!**\n\n"
            f"📊 **Результаты проверки:**\n"
            f"✅ {access_message}\n\n"
            f"📁 **Папка для бэкапов:** `{config['backup_path']}`\n\n"
            f"🎉 **Все функции бота будут работать корректно!**"
        )
        await status_msg.delete()
        await message.reply(result_message, parse_mode='Markdown')
    else:
        delete_user_token(user_id)
        await status_msg.edit_text(
            f"❌ **Токен недействителен!**\n\n"
            f"Причина: {access_message}",
            parse_mode='Markdown'
        )
    
    await state.finish()
    await cmd_start(message, state)


@dp.callback_query_handler(lambda c: c.data == "auth_yandex")
async def auth_yandex(callback: types.CallbackQuery):
    auth_url = get_auth_url()
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔑 Перейти к авторизации", url=auth_url))
    keyboard.add(InlineKeyboardButton("✅ Я получил код", callback_data="enter_code"))
    
    await bot.send_message(
        callback.from_user.id,
        f"🔑 **Авторизация Яндекс.Диска**\n\n"
        f"1️⃣ Нажмите на кнопку ниже\n"
        f"2️⃣ Войдите в аккаунт и разрешите доступ\n"
        f"3️⃣ Скопируйте код из адресной строки (часть после `code=`)\n"
        f"4️⃣ Нажмите «Я получил код» и отправьте его",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.message_handler(state=AuthStates.waiting_for_yandex_code)
async def receive_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    if not code:
        await message.reply("❌ **Ошибка!** Отправьте код авторизации.", parse_mode='Markdown')
        return
    
    status_msg = await message.reply("⏳ **Получение токена...**", parse_mode='Markdown')
    token = await get_access_token(code)
    
    if token:
        save_user_token(user_id, token)
        await status_msg.edit_text("⏳ **Проверка доступа...**")
        
        access, access_message, yandex_disk = await check_yandex_access_with_test(user_id)
        
        if access:
            yandex_disk.create_folder(config['backup_path'])
            result_message = (
                f"✅ **Авторизация успешна!**\n\n"
                f"📊 **Результаты проверки:**\n"
                f"✅ {access_message}\n\n"
                f"📁 **Папка для бэкапов:** `{config['backup_path']}`"
            )
        else:
            delete_user_token(user_id)
            result_message = (
                f"⚠️ **Токен получен, но доступ ограничен!**\n\n"
                f"❌ {access_message}\n\n"
                f"⚠️ Проверьте настройки приложения"
            )
        
        await status_msg.delete()
        await message.reply(result_message, parse_mode='Markdown')
        await cmd_start(message, state)
    else:
        await status_msg.edit_text(f"❌ **Ошибка авторизации!**\n\nНеверный код", parse_mode='Markdown')
    
    await state.finish()


@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление")
async def add_notification_start(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply(
        "✏️ **Введите текст уведомления:**\n\n"
        "⏰ **У вас есть 3 минуты** на ввод текста",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_text.set()


@dp.message_handler(state=NotificationStates.waiting_for_text)
async def get_notification_text(message: types.Message, state: FSMContext):
    if not message.text:
        await message.reply("❌ **Ошибка!** Введите текст уведомления.", parse_mode='Markdown')
        return
    
    await state.update_data(text=message.text)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("📅 Каждый день", callback_data="time_daily"),
        InlineKeyboardButton("📆 Дни недели", callback_data="time_weekdays")
    )
    
    await message.reply(
        "⏱️ **Через сколько уведомить?**\n\n"
        "• В часах/днях/месяцах - разовое уведомление\n"
        "• Конкретная дата - разовое уведомление\n"
        "• Каждый день - ежедневное уведомление\n"
        "• Дни недели - еженедельное уведомление\n\n"
        "⏰ **У вас есть 3 минуты** на выбор",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_time_type.set()


@dp.callback_query_handler(lambda c: c.data.startswith('time_'), state=NotificationStates.waiting_for_time_type)
async def get_time_type(callback: types.CallbackQuery, state: FSMContext):
    time_type = callback.data.replace('time_', '')
    await state.update_data(time_type=time_type)
    
    if time_type == 'hours':
        await bot.send_message(
            callback.from_user.id,
            "⌛ **Введите количество часов:**\n\n⏰ **У вас есть 3 минуты**",
            parse_mode='Markdown'
        )
        await NotificationStates.waiting_for_hours.set()
    elif time_type == 'days':
        await bot.send_message(
            callback.from_user.id,
            "📅 **Введите количество дней:**\n\n⏰ **У вас есть 3 минуты**",
            parse_mode='Markdown'
        )
        await NotificationStates.waiting_for_days.set()
    elif time_type == 'months':
        await bot.send_message(
            callback.from_user.id,
            "📆 **Введите количество месяцев:**\n\n⏰ **У вас есть 3 минуты**",
            parse_mode='Markdown'
        )
        await NotificationStates.waiting_for_months.set()
    elif time_type == 'specific':
        await bot.send_message(
            callback.from_user.id,
            "🗓️ **Введите дату**\n\n"
            "Поддерживаемые форматы:\n"
            "• ДД.ММ.ГГГГ (например: 31.12.2025)\n"
            "• ДД.ММ.ГГ (например: 31.12.25)\n"
            "• ДД.ММ (например: 31.12) - текущий или следующий год\n"
            "• ДД (например: 31) - текущий или следующий месяц\n\n"
            "⏰ **У вас есть 3 минуты**",
            parse_mode='Markdown'
        )
        await NotificationStates.waiting_for_specific_date.set()
    elif time_type == 'daily':
        await bot.send_message(
            callback.from_user.id,
            "🕐 **Введите время** (ЧЧ:ММ):\n"
            "Например: `09:00` или `18:30`\n\n"
            "⏰ **У вас есть 3 минуты**",
            parse_mode='Markdown'
        )
        await NotificationStates.waiting_for_specific_time.set()
    elif time_type == 'weekdays':
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("Понедельник", callback_data="weekday_monday"),
            InlineKeyboardButton("Вторник", callback_data="weekday_tuesday"),
            InlineKeyboardButton("Среда", callback_data="weekday_wednesday"),
            InlineKeyboardButton("Четверг", callback_data="weekday_thursday"),
            InlineKeyboardButton("Пятница", callback_data="weekday_friday"),
            InlineKeyboardButton("Суббота", callback_data="weekday_saturday"),
            InlineKeyboardButton("Воскресенье", callback_data="weekday_sunday"),
            InlineKeyboardButton("✅ Готово", callback_data="weekday_done")
        )
        
        await bot.send_message(
            callback.from_user.id,
            "📆 **Выберите дни недели**\n\n"
            "Нажимайте на дни, чтобы выбрать. Когда закончите - нажмите «Готово»",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.update_data(selected_weekdays=[])
        await NotificationStates.waiting_for_weekdays.set()
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('weekday_') and not c.data == 'weekday_done', state=NotificationStates.waiting_for_weekdays)
async def select_weekday(callback: types.CallbackQuery, state: FSMContext):
    weekday = callback.data.replace('weekday_', '')
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    
    if weekday in selected:
        selected.remove(weekday)
    else:
        selected.append(weekday)
    
    await state.update_data(selected_weekdays=selected)
    
    # Обновляем клавиатуру
    keyboard = InlineKeyboardMarkup(row_width=2)
    for wd, name in WEEKDAYS.items():
        if wd in selected:
            keyboard.add(InlineKeyboardButton(f"✅ {name}", callback_data=f"weekday_{wd}"))
        else:
            keyboard.add(InlineKeyboardButton(name, callback_data=f"weekday_{wd}"))
    keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="weekday_done"))
    
    await bot.edit_message_reply_markup(
        callback.from_user.id,
        callback.message.message_id,
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "weekday_done", state=NotificationStates.waiting_for_weekdays)
async def weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    
    if not selected:
        await callback.answer("Выберите хотя бы один день недели!")
        return
    
    weekday_names = [WEEKDAYS[wd] for wd in selected]
    await state.update_data(weekdays=selected, weekday_names=weekday_names)
    
    await bot.send_message(
        callback.from_user.id,
        f"📆 **Выбранные дни:** {', '.join(weekday_names)}\n\n"
        "🕐 **Введите время** (ЧЧ:ММ):\n"
        "Например: `09:00` или `18:30`\n\n"
        "⏰ **У вас есть 3 минуты**",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_weekday_time.set()
    await callback.answer()


async def save_notification(message: types.Message, state: FSMContext, notify_time: datetime, is_recurring: bool = False, recurring_data: dict = None):
    """Сохраняет уведомление и создает бэкап"""
    data = await state.get_data()
    next_num = len(notifications) + 1
    notif_id = str(next_num)
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    if notify_time.tzinfo is None:
        notify_time = tz.localize(notify_time)
    notify_time_utc = notify_time.astimezone(pytz.UTC)
    
    notifications[notif_id] = {
        'text': data['text'],
        'time': notify_time_utc.isoformat(),
        'created': get_current_time().isoformat(),
        'notified': False,
        'num': next_num,
        'is_recurring': is_recurring,
        'recurring_data': recurring_data or {}
    }
    
    save_data()
    
    if is_recurring and recurring_data:
        if recurring_data.get('type') == 'daily':
            await message.reply(
                f"✅ **Ежедневное уведомление #{next_num} создано!**\n"
                f"📝 {data['text']}\n"
                f"⏰ Ежедневно в {recurring_data.get('time', '00:00')}\n"
                f"📅 Первое срабатывание: {notify_time.strftime('%d.%m.%Y в %H:%M')}",
                parse_mode='Markdown'
            )
        elif recurring_data.get('type') == 'weekdays':
            days_str = ', '.join(recurring_data.get('days_names', []))
            await message.reply(
                f"✅ **Еженедельное уведомление #{next_num} создано!**\n"
                f"📝 {data['text']}\n"
                f"⏰ {days_str} в {recurring_data.get('time', '00:00')}\n"
                f"📅 Первое срабатывание: {notify_time.strftime('%d.%m.%Y в %H:%M')}",
                parse_mode='Markdown'
            )
    else:
        await message.reply(
            f"✅ **Уведомление #{next_num} создано!**\n"
            f"📝 {data['text']}\n"
            f"⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📅 Сработает: {notify_time.strftime('%d.%m.%Y в %H:%M')}",
            parse_mode='Markdown'
        )
    
    if ADMIN_ID:
        success, _, location = await create_backup(ADMIN_ID)
        if success:
            msg = await message.reply(f"✅ **Бэкап создан** ({location})")
            await asyncio.sleep(3)
            await msg.delete()
    
    await state.finish()


@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число часов.", parse_mode='Markdown')
            return
        notify_time = get_current_time() + timedelta(hours=hours)
        await save_notification(message, state, notify_time, False, None)
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число часов.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_days)
async def set_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число дней.", parse_mode='Markdown')
            return
        notify_time = get_current_time() + timedelta(days=days)
        await save_notification(message, state, notify_time, False, None)
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число дней.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_months)
async def set_months(message: types.Message, state: FSMContext):
    try:
        months = int(message.text)
        if months <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число месяцев.", parse_mode='Markdown')
            return
        days = months * 30
        notify_time = get_current_time() + timedelta(days=days)
        await save_notification(message, state, notify_time, False, None)
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число месяцев.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def set_specific_date(message: types.Message, state: FSMContext):
    parsed_date = parse_date(message.text)
    
    if not parsed_date:
        await message.reply(
            "❌ **Ошибка!** Неверный формат даты!\n\n"
            "Поддерживаемые форматы:\n"
            "• ДД.ММ.ГГГГ (например: 31.12.2025)\n"
            "• ДД.ММ.ГГ (например: 31.12.25)\n"
            "• ДД.ММ (например: 31.12)\n"
            "• ДД (например: 31)",
            parse_mode='Markdown'
        )
        return
    
    # Запрашиваем время
    await state.update_data(specific_date=parsed_date)
    await message.reply(
        f"📅 **Дата:** {parsed_date.strftime('%d.%m.%Y')}\n\n"
        "🕐 **Введите время** (ЧЧ:ММ):\n"
        "Например: `09:00` или `18:30`\n\n"
        "⏰ **У вас есть 3 минуты**",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_specific_time.set()


@dp.message_handler(state=NotificationStates.waiting_for_specific_time)
async def set_specific_time(message: types.Message, state: FSMContext):
    time_obj = parse_time(message.text)
    
    if not time_obj:
        await message.reply(
            "❌ **Ошибка!** Неверный формат времени!\n\n"
            "Используйте формат `ЧЧ:ММ`\n"
            "Например: `09:00` или `18:30`",
            parse_mode='Markdown'
        )
        return
    
    data = await state.get_data()
    
    if 'specific_date' in data:
        # Конкретная дата
        notify_time = data['specific_date'].replace(hour=time_obj.hour, minute=time_obj.minute)
        if notify_time <= get_current_time():
            await message.reply("❌ **Ошибка!** Дата и время должны быть в будущем!", parse_mode='Markdown')
            return
        await save_notification(message, state, notify_time, False, None)
    elif 'weekdays' in data:
        # Дни недели
        weekdays = data.get('weekdays', [])
        weekday_names = data.get('weekday_names', [])
        
        # Находим ближайший выбранный день недели
        now = get_current_time()
        target_weekday = None
        for wd in weekdays:
            wd_num = list(WEEKDAYS.keys()).index(wd)
            if wd_num >= now.weekday():
                target_weekday = wd_num
                break
        if target_weekday is None:
            target_weekday = list(WEEKDAYS.keys()).index(weekdays[0])
        
        notify_time = get_next_weekday(target_weekday, time_obj)
        
        recurring_data = {
            'type': 'weekdays',
            'days': weekdays,
            'days_names': weekday_names,
            'time': message.text
        }
        await save_notification(message, state, notify_time, True, recurring_data)
    else:
        # Ежедневное уведомление
        now = get_current_time()
        notify_time = now.replace(hour=time_obj.hour, minute=time_obj.minute, second=0, microsecond=0)
        if notify_time <= now:
            notify_time += timedelta(days=1)
        
        recurring_data = {
            'type': 'daily',
            'time': message.text
        }
        await save_notification(message, state, notify_time, True, recurring_data)


@dp.message_handler(state=NotificationStates.waiting_for_weekday_time)
async def set_weekday_time(message: types.Message, state: FSMContext):
    time_obj = parse_time(message.text)
    
    if not time_obj:
        await message.reply(
            "❌ **Ошибка!** Неверный формат времени!\n\n"
            "Используйте формат `ЧЧ:ММ`\n"
            "Например: `09:00` или `18:30`",
            parse_mode='Markdown'
        )
        return
    
    data = await state.get_data()
    weekdays = data.get('weekdays', [])
    weekday_names = data.get('weekday_names', [])
    
    # Находим ближайший выбранный день недели
    now = get_current_time()
    target_weekday = None
    for wd in weekdays:
        wd_num = list(WEEKDAYS.keys()).index(wd)
        if wd_num >= now.weekday():
            target_weekday = wd_num
            break
    if target_weekday is None:
        target_weekday = list(WEEKDAYS.keys()).index(weekdays[0])
    
    notify_time = get_next_weekday(target_weekday, time_obj)
    
    recurring_data = {
        'type': 'weekdays',
        'days': weekdays,
        'days_names': weekday_names,
        'time': message.text
    }
    await save_notification(message, state, notify_time, True, recurring_data)


@dp.message_handler(lambda m: m.text == "📋 Список уведомлений")
async def list_notifications(message: types.Message, state: FSMContext):
    await state.finish()
    
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    sorted_notifs = sorted(notifications.items(), key=lambda x: x[1]['time'])
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    for notif_id, notif in sorted_notifs:
        notify_time = datetime.fromisoformat(notif['time'])
        if notify_time.tzinfo is None:
            notify_time = pytz.UTC.localize(notify_time)
        local_time = notify_time.astimezone(tz)
        now = get_current_time()
        
        if notif.get('notified', False):
            status = "✅ ВЫПОЛНЕНО"
            status_emoji = "✅"
        elif now >= local_time:
            status = "⏰ ПРОСРОЧЕНО"
            status_emoji = "⚠️"
        else:
            status = "⏳ ОЖИДАЕТ"
            status_emoji = "⏳"
        
        # Информация о повторении
        recurring_info = ""
        if notif.get('is_recurring', False):
            rec_data = notif.get('recurring_data', {})
            if rec_data.get('type') == 'daily':
                recurring_info = f"\n🔄 **Повтор:** Ежедневно в {rec_data.get('time', '00:00')}"
            elif rec_data.get('type') == 'weekdays':
                days_str = ', '.join(rec_data.get('days_names', []))
                recurring_info = f"\n🔄 **Повтор:** {days_str} в {rec_data.get('time', '00:00')}"
        
        time_left = ""
        if not notif.get('notified', False) and now < local_time:
            delta = local_time - now
            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60
            
            if days > 0:
                time_left = f"\n📅 **Осталось:** {days} дн. {hours} ч."
            elif hours > 0:
                time_left = f"\n📅 **Осталось:** {hours} ч. {minutes} мин."
            else:
                time_left = f"\n📅 **Осталось:** {minutes} мин."
        
        text = (
            f"{status_emoji} **Уведомление #{notif.get('num', notif_id)}**\n"
            f"📝 **Текст:** {notif['text']}\n"
            f"⏰ **Время:** {local_time.strftime('%d.%m.%Y в %H:%M')}{recurring_info}\n"
            f"📊 **Статус:** {status}{time_left}"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
        )
        
        await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
    
    await message.reply(
        f"📊 **Всего уведомлений:** {len(notifications)}\n"
        f"💡 **Активных:** {sum(1 for n in notifications.values() if not n.get('notified', False))}",
        parse_mode='Markdown'
    )


# Остальные обработчики (edit, delete, snooze, settings, backup и т.д.) остаются без изменений
# Для экономии места они не включены, но должны быть добавлены из предыдущей версии