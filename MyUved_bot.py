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

# Дни недели для кнопок
WEEKDAYS_BUTTONS = [
    ("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)
]

WEEKDAYS_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}


def get_current_time():
    """Возвращает текущее время с учетом часового пояса"""
    timezone_str = config.get('timezone', 'Europe/Moscow')
    tz = pytz.timezone(timezone_str)
    return datetime.now(tz)


def parse_date(date_str: str) -> Optional[datetime]:
    """Парсит дату в различных форматах"""
    date_str = date_str.strip()
    now = get_current_time()
    current_year = now.year
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    # Формат ДД.ММ.ГГГГ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        day, month, year, hour, minute = match.groups()
        year = int(year)
        if year < 100:
            year = 2000 + year
        try:
            return tz.localize(datetime(year, int(month), int(day), int(hour), int(minute)))
        except:
            return None
    
    # Формат ДД.ММ.ГГГГ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', date_str)
    if match:
        day, month, year = match.groups()
        year = int(year)
        if year < 100:
            year = 2000 + year
        try:
            return tz.localize(datetime(year, int(month), int(day), now.hour, now.minute))
        except:
            return None
    
    # Формат ДД.ММ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        day, month, hour, minute = match.groups()
        year = current_year
        try:
            result = tz.localize(datetime(year, int(month), int(day), int(hour), int(minute)))
            if result < now:
                result = tz.localize(datetime(year + 1, int(month), int(day), int(hour), int(minute)))
            return result
        except:
            return None
    
    # Формат ДД.ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})$', date_str)
    if match:
        day, month = match.groups()
        try:
            result = tz.localize(datetime(current_year, int(month), int(day), now.hour, now.minute))
            if result < now:
                result = tz.localize(datetime(current_year + 1, int(month), int(day), now.hour, now.minute))
            return result
        except:
            return None
    
    return None


def get_next_weekday(target_weekdays: List[int], hour: int, minute: int) -> Optional[datetime]:
    """Получает следующую дату по дням недели"""
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    for i in range(1, 15):
        next_date = now + timedelta(days=i)
        if next_date.weekday() in target_weekdays:
            result = tz.localize(datetime(next_date.year, next_date.month, next_date.day, hour, minute))
            if result > now:
                return result
    
    return None


def get_auth_url() -> str:
    """Правильное формирование URL для авторизации"""
    params = {
        "response_type": "code",  # Исправлено: response_type вместо responsetype
        "client_id": CLIENT_ID,   # Исправлено: client_id вместо clientid
        "redirect_uri": REDIRECT_URI
    }
    return f"{YANDEX_OAUTH_URL}?{urlencode(params)}"


def get_token_url() -> str:
    """Получение URL для получения токена напрямую"""
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
    
    def list_folders(self, folder_path="/"):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path, "limit": 100}
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            
            if response.status_code == 200:
                items = response.json().get("_embedded", {}).get("items", [])
                folders = [item for item in items if item.get("type") == "dir"]
                return folders
            return []
        except Exception as e:
            print(f"Ошибка получения списка папок: {e}")
            return []
    
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
    waiting_for_auth_method = State()
    waiting_for_yandex_code = State()
    waiting_for_direct_token = State()


class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time_type = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_weekdays = State()
    waiting_for_weekday_time = State()
    waiting_for_every_day_time = State()
    waiting_for_edit_notification = State()
    waiting_for_edit_text = State()
    waiting_for_edit_time = State()


class SettingsStates(StatesGroup):
    waiting_for_backup_path = State()
    waiting_for_folder_selection = State()
    waiting_for_new_folder_name = State()
    waiting_for_max_backups = State()
    waiting_for_check_time = State()
    waiting_for_upload_backup = State()
    waiting_for_timezone = State()
    waiting_for_restore_source = State()
    waiting_for_backup_selection = State()


# Функция для автоматического удаления сообщений
async def auto_delete_message(chat_id: int, message_id: int, delay: int = 180):
    """Автоматически удаляет сообщение через заданное время"""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass


async def send_with_auto_delete(chat_id: int, text: str, parse_mode: str = 'Markdown', reply_markup=None, delay: int = 180):
    """Отправляет сообщение и автоматически удаляет его через заданное время"""
    msg = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    asyncio.create_task(auto_delete_message(chat_id, msg.message_id, delay))
    return msg


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


async def browse_folders(user_id: int, current_path: str = "/") -> List[Dict]:
    try:
        token = get_user_token(user_id)
        if not token:
            return []
        
        yandex_disk = YandexDiskAPI(token)
        folders = yandex_disk.list_folders(current_path)
        return folders
    except Exception as e:
        print(f"Ошибка просмотра папок: {e}")
        return []


async def check_notifications():
    """Проверка уведомлений с поддержкой повторения каждый час для невыполненных"""
    global notifications_enabled
    while True:
        if notifications_enabled:
            now = get_current_time()
            
            for notif_id, notif in list(notifications.items()):
                # Проверка для повторяющихся уведомлений
                if notif.get('repeat_type') and notif.get('repeat_type') != 'no':
                    repeat_type = notif.get('repeat_type')
                    last_trigger = datetime.fromisoformat(notif.get('last_trigger', '2000-01-01T00:00:00'))
                    if last_trigger.tzinfo is None:
                        last_trigger = pytz.UTC.localize(last_trigger)
                    
                    should_trigger = False
                    
                    if repeat_type == 'every_day':
                        today_trigger = now.replace(hour=notif.get('repeat_hour', 0), minute=notif.get('repeat_minute', 0), second=0, microsecond=0)
                        if now >= today_trigger and last_trigger.date() < now.date():
                            should_trigger = True
                    
                    elif repeat_type == 'every_week':
                        today_trigger = now.replace(hour=notif.get('repeat_hour', 0), minute=notif.get('repeat_minute', 0), second=0, microsecond=0)
                        if now >= today_trigger and last_trigger.date() < now.date() and now.weekday() == notif.get('repeat_weekday', 0):
                            should_trigger = True
                    
                    elif repeat_type == 'every_month':
                        today_trigger = now.replace(hour=notif.get('repeat_hour', 0), minute=notif.get('repeat_minute', 0), second=0, microsecond=0)
                        if now >= today_trigger and last_trigger.date() < now.date() and now.day == notif.get('repeat_month_day', 1):
                            should_trigger = True
                    
                    elif repeat_type == 'weekdays':
                        today_trigger = now.replace(hour=notif.get('repeat_hour', 0), minute=notif.get('repeat_minute', 0), second=0, microsecond=0)
                        if now >= today_trigger and last_trigger.date() < now.date() and now.weekday() in notif.get('weekdays_list', []):
                            should_trigger = True
                    
                    if should_trigger:
                        keyboard = InlineKeyboardMarkup(row_width=2)
                        keyboard.add(
                            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{notif_id}"),
                            InlineKeyboardButton("⏰ Напомнить через час", callback_data=f"snooze_{notif_id}_1")
                        )
                        
                        await bot.send_message(
                            ADMIN_ID,
                            f"🔔 НАПОМИНАНИЕ!\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}",
                            reply_markup=keyboard
                        )
                        
                        notif['last_trigger'] = now.isoformat()
                        save_data()
                else:
                    # Обычное уведомление (не повторяющееся)
                    if notif.get('time'):
                        notify_time = datetime.fromisoformat(notif['time'])
                        if notify_time.tzinfo is None:
                            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                            notify_time = tz.localize(notify_time)
                        
                        if now >= notify_time and not notif.get('notified', False):
                            last_reminder = notif.get('last_reminder')
                            if last_reminder:
                                last_reminder = datetime.fromisoformat(last_reminder)
                                if last_reminder.tzinfo is None:
                                    last_reminder = pytz.UTC.localize(last_reminder)
                                if (now - last_reminder).total_seconds() < 3600:
                                    continue
                            
                            keyboard = InlineKeyboardMarkup(row_width=2)
                            keyboard.add(
                                InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{notif_id}"),
                                InlineKeyboardButton("⏰ Напомнить через час", callback_data=f"snooze_{notif_id}_1")
                            )
                            
                            await bot.send_message(
                                ADMIN_ID,
                                f"🔔 НАПОМИНАНИЕ!\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}",
                                reply_markup=keyboard
                            )
                            
                            notifications[notif_id]['last_reminder'] = now.isoformat()
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
        KeyboardButton("⚙️ Настройки")
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
            backups = await get_yadisk_backups(user_id)
            backup_text = ""
            if backups:
                backup_text = f"\n\n📦 **Найдено бэкапов на Яндекс.Диске:** {len(backups)}\nХотите восстановить данные из бэкапа?"
                keyboard = InlineKeyboardMarkup(row_width=2)
                keyboard.add(
                    InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"),
                    InlineKeyboardButton("❌ Нет, спасибо", callback_data="decline_restore")
                )
                await message.reply(
                    f"✅ **Доступ к Яндекс.Диску имеется!**\n\n"
                    f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n"
                    f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}{backup_text}",
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await message.reply(
                    f"✅ **Доступ к Яндекс.Диску имеется!**\n\n"
                    f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n"
                    f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}",
                    parse_mode='Markdown'
                )
        else:
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth")
            )
            
            await message.reply(
                f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
                f"Причина: {access_message}\n\n"
                f"Для работы бэкапов необходимо авторизоваться.\n\n"
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth")
        )
        
        await message.reply(
            f"👋 **Добро пожаловать!**\n\n"
            f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов необходимо авторизоваться.\n\n"
            f"Нажмите кнопку ниже для авторизации:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await message.reply(
        "👋 **Выберите действие:**",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )


@dp.callback_query_handler(lambda c: c.data == "start_auth")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔑 Через код авторизации", callback_data="auth_method_code"),
        InlineKeyboardButton("🔓 Через токен напрямую", callback_data="auth_method_token")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_auth"))
    
    await bot.send_message(
        callback.from_user.id,
        "🔐 **Выберите способ авторизации:**\n\n"
        "• **Через код** - стандартный способ, нужно получить код на Яндексе\n"
        "• **Через токен** - отладочный способ, токен можно получить по ссылке",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_auth_method.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "auth_method_code", state=AuthStates.waiting_for_auth_method)
async def auth_method_code(callback: types.CallbackQuery, state: FSMContext):
    auth_url = get_auth_url()
    
    await bot.send_message(
        callback.from_user.id,
        f"🔑 **Авторизация через код**\n\n"
        f"1️⃣ Перейдите по ссылке: {auth_url}\n"
        f"2️⃣ Войдите в аккаунт Яндекс\n"
        f"3️⃣ Разрешите доступ\n"
        f"4️⃣ Скопируйте код из адресной строки (часть после `code=`)\n"
        f"5️⃣ **Отправьте полученный код в чат**\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод кода",
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "auth_method_token", state=AuthStates.waiting_for_auth_method)
async def auth_method_token(callback: types.CallbackQuery, state: FSMContext):
    token_url = get_token_url()
    
    await bot.send_message(
        callback.from_user.id,
        f"🔓 **Авторизация через токен**\n\n"
        f"1️⃣ Перейдите по ссылке: {token_url}\n"
        f"2️⃣ Войдите в аккаунт Яндекс\n"
        f"3️⃣ Разрешите доступ\n"
        f"4️⃣ Скопируйте токен из адресной строки (часть после `access_token=`)\n"
        f"5️⃣ **Отправьте полученный токен в чат**\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод токена",
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_direct_token.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_auth", state=AuthStates.waiting_for_auth_method)
async def cancel_auth(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await cmd_start(callback.message, state)
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
        
        backups = await get_yadisk_backups(user_id)
        if backups:
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"),
                InlineKeyboardButton("❌ Нет, спасибо", callback_data="decline_restore")
            )
            await message.reply(
                f"📦 **Найдено бэкапов на Яндекс.Диске:** {len(backups)}\nХотите восстановить данные из бэкапа?",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        delete_user_token(user_id)
        await status_msg.edit_text(
            f"❌ **Токен недействителен!**\n\n"
            f"Причина: {access_message}\n\n"
            f"Попробуйте другой способ авторизации.",
            parse_mode='Markdown'
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🔑 Попробовать через код", callback_data="auth_method_code"),
            InlineKeyboardButton("🔙 Назад", callback_data="start_auth")
        )
        await message.reply(
            "⚠️ **Авторизация не удалась!**\n\n"
            "Попробуйте другой способ:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await state.finish()


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
            await status_msg.delete()
            await message.reply(result_message, parse_mode='Markdown')
            
            backups = await get_yadisk_backups(user_id)
            if backups:
                keyboard = InlineKeyboardMarkup(row_width=2)
                keyboard.add(
                    InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"),
                    InlineKeyboardButton("❌ Нет, спасибо", callback_data="decline_restore")
                )
                await message.reply(
                    f"📦 **Найдено бэкапов на Яндекс.Диске:** {len(backups)}\nХотите восстановить данные из бэкапа?",
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
        else:
            delete_user_token(user_id)
            await status_msg.edit_text(
                f"⚠️ **Токен получен, но доступ ограничен!**\n\n"
                f"❌ {access_message}\n\n"
                f"⚠️ Проверьте настройки приложения\n\n"
                f"Попробуйте другой способ авторизации.",
                parse_mode='Markdown'
            )
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("🔓 Попробовать через токен", callback_data="auth_method_token"),
                InlineKeyboardButton("🔙 Назад", callback_data="start_auth")
            )
            await message.reply(
                "⚠️ **Авторизация не удалась!**\n\n"
                "Попробуйте другой способ:",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        await status_msg.edit_text(
            f"❌ **Ошибка авторизации!**\n\n"
            f"Неверный код\n\n"
            f"Попробуйте другой способ.",
            parse_mode='Markdown'
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🔓 Попробовать через токен", callback_data="auth_method_token"),
            InlineKeyboardButton("🔙 Назад", callback_data="start_auth")
        )
        await message.reply(
            "⚠️ **Авторизация не удалась!**\n\n"
            "Попробуйте другой способ:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "offer_restore")
async def offer_restore_handler(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    backups = await get_yadisk_backups(user_id)
    
    if not backups:
        await bot.send_message(callback.from_user.id, "📭 **Нет доступных бэкапов для восстановления**", parse_mode='Markdown')
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for backup in backups:
        backup_time = backup['name'].replace('backup_', '').replace('.json', '')
        try:
            backup_date = datetime.strptime(backup_time, '%Y%m%d_%H%M%S')
            button_text = f"📦 {backup_date.strftime('%d.%m.%Y %H:%M:%S')}"
        except:
            button_text = f"📦 {backup['name']}"
        keyboard.add(InlineKeyboardButton(button_text, callback_data=f"restore_backup_{backup['name']}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="decline_restore"))
    
    await bot.send_message(
        callback.from_user.id,
        "📦 **Выберите бэкап для восстановления:**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "decline_restore")
async def decline_restore_handler(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "✅ **Восстановление отменено**", parse_mode='Markdown')
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("restore_backup_"))
async def restore_selected_backup(callback: types.CallbackQuery, state: FSMContext):
    backup_name = callback.data.replace("restore_backup_", "")
    user_id = callback.from_user.id
    
    status_msg = await bot.send_message(
        callback.from_user.id,
        "⏳ **Восстановление из бэкапа...**",
        parse_mode='Markdown'
    )
    
    if await restore_from_yadisk_backup(backup_name, user_id):
        await status_msg.edit_text(
            "✅ **Данные успешно восстановлены из бэкапа!**\n\n"
            f"📝 Уведомлений: {len(notifications)}",
            parse_mode='Markdown'
        )
    else:
        await status_msg.edit_text(
            "❌ **Ошибка восстановления!**\n\n"
            "Не удалось восстановить данные из бэкапа.",
            parse_mode='Markdown'
        )
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "auth_yandex")
async def auth_yandex(callback: types.CallbackQuery):
    await start_auth(callback, None)
    await callback.answer()


@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление")
async def add_notification_start(message: types.Message, state: FSMContext):
    await state.finish()
    await send_with_auto_delete(
        message.chat.id,
        "✏️ **Введите текст уведомления:**\n\n"
        "⏰ **У вас есть 3 минуты** на ввод текста\n\n"
        "💡 Для отмены отправьте /cancel",
        delay=180
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
        InlineKeyboardButton("📅 Каждый день", callback_data="time_every_day"),
        InlineKeyboardButton("📆 По дням недели", callback_data="time_weekdays"),
        InlineKeyboardButton("🔙 Назад к тексту", callback_data="back_to_text")
    )
    
    await send_with_auto_delete(
        message.chat.id,
        "⏱️ **Когда уведомить?**\n\n"
        "⏰ **У вас есть 3 минуты** на выбор\n\n"
        "💡 Для отмены отправьте /cancel",
        reply_markup=keyboard,
        delay=180
    )
    await NotificationStates.waiting_for_time_type.set()


@dp.callback_query_handler(lambda c: c.data == "back_to_text", state=NotificationStates.waiting_for_time_type)
async def back_to_text(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback.from_user.id, "✏️ **Введите новый текст уведомления:**", parse_mode='Markdown')
    await NotificationStates.waiting_for_text.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('time_'), state=NotificationStates.waiting_for_time_type)
async def get_time_type(callback: types.CallbackQuery, state: FSMContext):
    time_type = callback.data.replace('time_', '')
    await state.update_data(time_type=time_type)
    
    if time_type == 'hours':
        await send_with_auto_delete(
            callback.from_user.id,
            "⌛ **Введите количество часов:**\n\n"
            "📝 Например: `5` или `24`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await NotificationStates.waiting_for_hours.set()
    elif time_type == 'days':
        await send_with_auto_delete(
            callback.from_user.id,
            "📅 **Введите количество дней:**\n\n"
            "📝 Например: `7` или `30`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await NotificationStates.waiting_for_days.set()
    elif time_type == 'months':
        await send_with_auto_delete(
            callback.from_user.id,
            "📆 **Введите количество месяцев:**\n\n"
            "📝 Например: `1` или `6`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await NotificationStates.waiting_for_months.set()
    elif time_type == 'specific':
        await send_with_auto_delete(
            callback.from_user.id,
            "🗓️ **Введите дату**\n\n"
            "📝 **Поддерживаемые форматы:**\n"
            "• `31.12.2025 23:59` - дата и время\n"
            "• `31.12.2025` - только дата (время текущее)\n"
            "• `06.04 9:00` - дата и время (текущий год)\n"
            "• `31.12` - день и месяц (ближайший в будущем)\n\n"
            "📌 Пример: `06.04 9:00` или `31.12.2025 23:59`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await NotificationStates.waiting_for_specific_date.set()
    elif time_type == 'every_day':
        await send_with_auto_delete(
            callback.from_user.id,
            "⏰ **Введите время для ежедневного уведомления**\n\n"
            "📝 Формат: `ЧЧ:ММ`\n\n"
            "📝 Примеры: `09:00` или `18:30`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await NotificationStates.waiting_for_every_day_time.set()
    elif time_type == 'weekdays':
        keyboard = InlineKeyboardMarkup(row_width=3)
        for name, day in WEEKDAYS_BUTTONS:
            keyboard.add(InlineKeyboardButton(name, callback_data=f"wd_{day}"))
        keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="wd_done_for_time"))
        keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_time_type"))
        
        await bot.send_message(
            callback.from_user.id,
            "📅 **Выберите дни недели**\n\n"
            "Нажимайте на дни, чтобы выбрать/отменить.\n"
            "Когда закончите, нажмите «✅ Готово»\n\n"
            "⏰ **У вас есть 3 минуты**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.update_data(selected_weekdays=[])
        await NotificationStates.waiting_for_weekdays.set()
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "back_to_time_type", state=NotificationStates.waiting_for_weekdays)
async def back_to_time_type(callback: types.CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("📅 Каждый день", callback_data="time_every_day"),
        InlineKeyboardButton("📆 По дням недели", callback_data="time_weekdays"),
        InlineKeyboardButton("🔙 Назад к тексту", callback_data="back_to_text")
    )
    
    await bot.send_message(
        callback.from_user.id,
        "⏱️ **Когда уведомить?**\n\n"
        "⏰ **У вас есть 3 минуты** на выбор",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_time_type.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('wd_'), state=NotificationStates.waiting_for_weekdays)
async def select_weekday(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.split('_')[1])
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    
    await state.update_data(selected_weekdays=selected)
    
    keyboard = InlineKeyboardMarkup(row_width=3)
    for name, d in WEEKDAYS_BUTTONS:
        text = f"✅ {name}" if d in selected else name
        keyboard.add(InlineKeyboardButton(text, callback_data=f"wd_{d}"))
    keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="wd_done_for_time"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_time_type"))
    
    selected_names = [WEEKDAYS_NAMES[d] for d in sorted(selected)]
    status_text = f"Выбрано: {', '.join(selected_names) if selected else 'ничего не выбрано'}"
    
    await bot.edit_message_text(
        f"📅 **Выберите дни недели**\n\n{status_text}\n\n"
        "Нажимайте на дни, чтобы выбрать/отменить.\n"
        "Когда закончите, нажмите «✅ Готово»\n\n"
        "⏰ **У вас есть 3 минуты**",
        callback.from_user.id,
        callback.message.message_id,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "wd_done_for_time", state=NotificationStates.waiting_for_weekdays)
async def weekdays_done_for_time(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день недели!")
        return
    
    await state.update_data(weekdays_list=selected)
    
    await send_with_auto_delete(
        callback.from_user.id,
        "⏰ **Введите время для уведомления**\n\n"
        "📝 Формат: `ЧЧ:ММ`\n\n"
        "📝 Примеры: `09:00` или `18:30`\n\n"
        "⏰ **У вас есть 3 минуты**\n\n"
        "💡 Для отмены отправьте /cancel",
        delay=180
    )
    await NotificationStates.waiting_for_weekday_time.set()
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_every_day_time)
async def set_every_day_time(message: types.Message, state: FSMContext):
    try:
        match = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
        if not match:
            await message.reply(
                "❌ **Ошибка!** Неверный формат времени.\n"
                "Используйте формат `ЧЧ:ММ` (например: `09:00` или `18:30`)",
                parse_mode='Markdown'
            )
            return
        
        hour, minute = map(int, match.groups())
        if hour > 23 or minute > 59:
            await message.reply("❌ **Ошибка!** Некорректное время (часы 0-23, минуты 0-59)", parse_mode='Markdown')
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
            'created': get_current_time().isoformat(),
            'notified': False,
            'num': next_num,
            'repeat_type': 'every_day',
            'repeat_hour': hour,
            'repeat_minute': minute,
            'last_trigger': (first_time - timedelta(days=1)).isoformat()
        }
        
        save_data()
        
        await message.reply(
            f"✅ **Уведомление #{next_num} создано!**\n"
            f"📝 {data['text']}\n"
            f"📅 **Тип:** Ежедневное\n"
            f"⏰ **Время:** {hour:02d}:{minute:02d}\n"
            f"🔁 Будет повторяться каждый день\n\n"
            f"ℹ️ Когда уведомление сработает:\n"
            f"• Нажмите «✅ Выполнено» - уведомление будет считаться выполненным\n"
            f"• Если не нажать «Выполнено», уведомление будет повторяться каждый час",
            parse_mode='Markdown'
        )
        
        if ADMIN_ID:
            success, _, location = await create_backup(ADMIN_ID)
            if success:
                msg = await message.reply(f"✅ **Бэкап создан** ({location})")
                await asyncio.sleep(3)
                await msg.delete()
        
        await state.finish()
    except Exception as e:
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_weekday_time)
async def set_weekday_time(message: types.Message, state: FSMContext):
    try:
        match = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
        if not match:
            await message.reply(
                "❌ **Ошибка!** Неверный формат времени.\n"
                "Используйте формат `ЧЧ:ММ` (например: `09:00` или `18:30`)",
                parse_mode='Markdown'
            )
            return
        
        hour, minute = map(int, match.groups())
        if hour > 23 or minute > 59:
            await message.reply("❌ **Ошибка!** Некорректное время (часы 0-23, минуты 0-59)", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        weekdays_list = data.get('weekdays_list', [])
        
        first_time = get_next_weekday(weekdays_list, hour, minute)
        
        if not first_time:
            await message.reply("❌ **Ошибка!** Не удалось определить дату", parse_mode='Markdown')
            return
        
        next_num = len(notifications) + 1
        notif_id = str(next_num)
        
        days_names = [WEEKDAYS_NAMES[d] for d in sorted(weekdays_list)]
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': first_time.isoformat(),
            'created': get_current_time().isoformat(),
            'notified': False,
            'num': next_num,
            'repeat_type': 'weekdays',
            'repeat_hour': hour,
            'repeat_minute': minute,
            'weekdays_list': weekdays_list,
            'last_trigger': (first_time - timedelta(days=7)).isoformat()
        }
        
        save_data()
        
        await message.reply(
            f"✅ **Уведомление #{next_num} создано!**\n"
            f"📝 {data['text']}\n"
            f"📅 **Тип:** По дням недели\n"
            f"📆 **Дни:** {', '.join(days_names)}\n"
            f"⏰ **Время:** {hour:02d}:{minute:02d}\n"
            f"🔁 Будет повторяться каждую неделю\n\n"
            f"ℹ️ Когда уведомление сработает:\n"
            f"• Нажмите «✅ Выполнено» - уведомление будет считаться выполненным\n"
            f"• Если не нажать «Выполнено», уведомление будет повторяться каждый час",
            parse_mode='Markdown'
        )
        
        if ADMIN_ID:
            success, _, location = await create_backup(ADMIN_ID)
            if success:
                msg = await message.reply(f"✅ **Бэкап создан** ({location})")
                await asyncio.sleep(3)
                await msg.delete()
        
        await state.finish()
    except Exception as e:
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


async def save_notification(message: types.Message, state: FSMContext, notify_time: datetime):
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
        'num': next_num
    }
    
    save_data()
    
    await message.reply(
        f"✅ **Уведомление #{next_num} создано!**\n"
        f"📝 {data['text']}\n"
        f"⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📅 Сработает: {notify_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
        f"ℹ️ Когда уведомление сработает:\n"
        f"• Нажмите «✅ Выполнено» - уведомление удалится\n"
        f"• Если не нажать «Выполнено», уведомление будет повторяться каждый час",
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
        await save_notification(message, state, notify_time)
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
        await save_notification(message, state, notify_time)
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
        await save_notification(message, state, notify_time)
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число месяцев.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def set_specific_date(message: types.Message, state: FSMContext):
    try:
        notify_time = parse_date(message.text)
        
        if notify_time is None:
            await message.reply(
                "❌ **Ошибка!** Неверный формат даты.\n\n"
                "📝 **Поддерживаемые форматы:**\n"
                "• `31.12.2025 23:59` - дата и время\n"
                "• `31.12.2025` - только дата (время текущее)\n"
                "• `06.04 9:00` - дата и время (текущий год)\n"
                "• `31.12` - день и месяц (ближайший в будущем)\n\n"
                "📌 Пример: `06.04 9:00` или `31.12.2025 23:59`",
                parse_mode='Markdown'
            )
            return
        
        if notify_time <= get_current_time():
            await message.reply("❌ **Ошибка!** Дата должна быть в будущем!", parse_mode='Markdown')
            return
        
        await save_notification(message, state, notify_time)
    except Exception as e:
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('complete_'))
async def handle_complete(callback: types.CallbackQuery):
    notif_id = callback.data.replace('complete_', '')
    
    if notif_id in notifications:
        notif_num = notifications[notif_id].get('num', notif_id)
        del notifications[notif_id]
        
        new_notifications = {}
        for i, (nid, notif) in enumerate(notifications.items(), 1):
            notif['num'] = i
            new_notifications[str(i)] = notif
        notifications.clear()
        notifications.update(new_notifications)
        
        save_data()
        
        try:
            await bot.delete_message(callback.from_user.id, callback.message.message_id)
        except:
            pass
        
        await bot.send_message(
            callback.from_user.id,
            f"✅ **Уведомление #{notif_num} выполнено и удалено!**",
            parse_mode='Markdown'
        )
        
        if ADMIN_ID:
            await create_backup(ADMIN_ID)
    else:
        await callback.answer("Уведомление уже обработано")
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('snooze_'))
async def handle_snooze(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    if len(parts) < 3:
        await callback.answer("Ошибка")
        return
    
    notif_id = parts[1]
    hours = int(parts[2])
    
    if notif_id in notifications:
        now = get_current_time()
        new_time = now + timedelta(hours=hours)
        
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        if new_time.tzinfo is None:
            new_time = tz.localize(new_time)
        new_time_utc = new_time.astimezone(pytz.UTC)
        
        notifications[notif_id]['time'] = new_time_utc.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['last_reminder'] = None
        save_data()
        
        try:
            await bot.delete_message(callback.from_user.id, callback.message.message_id)
        except:
            pass
        
        await bot.send_message(
            callback.from_user.id,
            f"⏰ **Уведомление отложено на {hours} час(ов)**\n"
            f"Новое время: {new_time.strftime('%H:%M %d.%m.%Y')}\n\n"
            f"ℹ️ Если не нажать «Выполнено», уведомление будет повторяться каждый час",
            parse_mode='Markdown'
        )
    
    await callback.answer()


@dp.message_handler(lambda m: m.text == "📋 Список уведомлений")
async def list_notifications(message: types.Message, state: FSMContext):
    await state.finish()
    
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    sorted_notifs = sorted(notifications.items(), key=lambda x: int(x[0]))
    
    for notif_id, notif in sorted_notifs:
        repeat_type = notif.get('repeat_type', 'no')
        
        repeat_text = ""
        if repeat_type == 'every_day':
            repeat_text = f"\n🔄 **Повтор:** Каждый день в {notif.get('repeat_hour', 0):02d}:{notif.get('repeat_minute', 0):02d}"
        elif repeat_type == 'every_week':
            weekday_name = WEEKDAYS_NAMES.get(notif.get('repeat_weekday', 0), '')
            repeat_text = f"\n🔄 **Повтор:** Каждую неделю в {weekday_name} {notif.get('repeat_hour', 0):02d}:{notif.get('repeat_minute', 0):02d}"
        elif repeat_type == 'every_month':
            repeat_text = f"\n🔄 **Повтор:** Каждый месяц {notif.get('repeat_month_day', 1)}-го числа в {notif.get('repeat_hour', 0):02d}:{notif.get('repeat_minute', 0):02d}"
        elif repeat_type == 'weekdays':
            days_names = [WEEKDAYS_NAMES[d] for d in notif.get('weekdays_list', [])]
            repeat_text = f"\n🔄 **Повтор:** По дням недели: {', '.join(days_names)} в {notif.get('repeat_hour', 0):02d}:{notif.get('repeat_minute', 0):02d}"
        elif repeat_type == 'no' and notif.get('time'):
            notify_time = datetime.fromisoformat(notif['time'])
            if notify_time.tzinfo is None:
                notify_time = pytz.UTC.localize(notify_time)
            local_time = notify_time.astimezone(tz)
            now = get_current_time()
            
            if notif.get('notified', False):
                status = "✅ ВЫПОЛНЕНО"
                status_emoji = "✅"
            elif now >= local_time:
                status = "⏰ ПРОСРОЧЕНО (повторяется каждый час)"
                status_emoji = "⚠️"
            else:
                status = "⏳ ОЖИДАЕТ"
                status_emoji = "⏳"
            
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
                f"⏰ **Время:** {local_time.strftime('%d.%m.%Y в %H:%M')}\n"
                f"📊 **Статус:** {status}{time_left}"
            )
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
            )
            
            await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
            continue
        
        text = (
            f"🔄 **Уведомление #{notif.get('num', notif_id)}**\n"
            f"📝 **Текст:** {notif['text']}\n"
            f"📊 **Статус:** АКТИВНО{repeat_text}\n\n"
            f"ℹ️ Если не нажать «Выполнено», уведомление будет повторяться каждый час"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
        )
        
        await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
    
    active_count = sum(1 for n in notifications.values() if not n.get('notified', False))
    await message.reply(
        f"📊 **Всего уведомлений:** {len(notifications)}\n"
        f"💡 **Активных:** {active_count}",
        parse_mode='Markdown'
    )


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_'))
async def edit_notification_start(callback: types.CallbackQuery, state: FSMContext):
    notif_id = callback.data.replace('edit_', '')
    
    if notif_id not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    
    await state.update_data(edit_id=notif_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить текст", callback_data="edit_text"),
        InlineKeyboardButton("⏰ Изменить время", callback_data="edit_time")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
    
    await bot.send_message(
        callback.from_user.id,
        f"✏️ **Что хотите изменить в уведомлении #{notifications[notif_id].get('num', notif_id)}?**\n\n"
        f"⏰ **У вас есть 3 минуты** на выбор действия",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "edit_text", state="*")
async def edit_notification_text(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(
        callback.from_user.id,
        "✏️ **Введите новый текст уведомления:**\n\n⏰ **У вас есть 3 минуты**",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_edit_text.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "edit_time", state="*")
async def edit_notification_time(callback: types.CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("📅 Каждый день", callback_data="time_every_day"),
        InlineKeyboardButton("📆 По дням недели", callback_data="time_weekdays")
    )
    
    await bot.send_message(
        callback.from_user.id,
        "⏱️ **Выберите новый период:**\n\n⏰ **У вас есть 3 минуты**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_edit_time.set()
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_edit_text)
async def save_edited_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    notif_id = data.get('edit_id')
    
    if notif_id and notif_id in notifications:
        notifications[notif_id]['text'] = message.text
        save_data()
        await message.reply(f"✅ **Текст уведомления изменен!**", parse_mode='Markdown')
        if ADMIN_ID:
            await create_backup(ADMIN_ID)
    else:
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "cancel_edit", state="*")
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(callback.from_user.id, "✅ **Редактирование отменено**", parse_mode='Markdown')
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def handle_delete_notification(callback: types.CallbackQuery):
    notif_id = callback.data.replace('delete_', '')
    
    if notif_id in notifications:
        notif_num = notifications[notif_id].get('num', notif_id)
        del notifications[notif_id]
        
        new_notifications = {}
        for i, (nid, notif) in enumerate(notifications.items(), 1):
            notif['num'] = i
            new_notifications[str(i)] = notif
        notifications.clear()
        notifications.update(new_notifications)
        
        save_data()
        
        try:
            await bot.delete_message(callback.from_user.id, callback.message.message_id)
        except:
            pass
        
        await bot.send_message(callback.from_user.id, f"✅ **Уведомление #{notif_num} удалено**", parse_mode='Markdown')
        
        if ADMIN_ID:
            await create_backup(ADMIN_ID)
    else:
        await callback.answer("Уведомление уже удалено")
    
    await callback.answer()


@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def settings_menu(message: types.Message, state: FSMContext):
    await state.finish()
    
    global notifications_enabled
    
    status_text = "🔕 Выкл" if not notifications_enabled else "🔔 Вкл"
    status_emoji = "🔕" if not notifications_enabled else "🔔"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(f"{status_emoji} Уведомления: {status_text}", callback_data="toggle_notifications"),
        InlineKeyboardButton("📁 Выбрать папку на Яндекс.Диске", callback_data="select_backup_folder"),
        InlineKeyboardButton("🔢 Максимум бэкапов", callback_data="set_max_backups"),
        InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time"),
        InlineKeyboardButton("🌍 Часовой пояс", callback_data="set_timezone"),
        InlineKeyboardButton("🔑 Авторизация", callback_data="auth_yandex"),
        InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup_manual"),
        InlineKeyboardButton("📤 Восстановить из бэкапа", callback_data="restore_backup"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="info")
    )
    
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=keyboard, parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "create_backup_manual")
async def manual_backup_settings(callback: types.CallbackQuery, state: FSMContext):
    if not ADMIN_ID:
        await bot.send_message(callback.from_user.id, "❌ Ошибка: ADMIN_ID не задан", parse_mode='Markdown')
        return
    
    status_msg = await bot.send_message(callback.from_user.id, "⏳ **Создание бэкапа...**", parse_mode='Markdown')
    success, _, location = await create_backup(ADMIN_ID)
    
    if success:
        await status_msg.edit_text(f"✅ **Бэкап создан** ({location})", parse_mode='Markdown')
        await asyncio.sleep(2)
        await status_msg.delete()
    else:
        await status_msg.edit_text("❌ **Ошибка создания бэкапа!**", parse_mode='Markdown')
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "select_backup_folder")
async def select_backup_folder_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    token = get_user_token(user_id)
    
    if not token:
        await bot.send_message(
            callback.from_user.id,
            "❌ **Нет доступа к Яндекс.Диску!**\n\n"
            "Сначала авторизуйтесь в настройках.",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    await state.update_data(current_folder="/")
    await show_folders(callback.from_user.id, "/", state)


async def show_folders(chat_id: int, current_path: str, state: FSMContext):
    user_id = chat_id
    token = get_user_token(user_id)
    
    if not token:
        await bot.send_message(chat_id, "❌ Нет доступа к Яндекс.Диску", parse_mode='Markdown')
        return
    
    yandex_disk = YandexDiskAPI(token)
    folders = yandex_disk.list_folders(current_path)
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    if current_path != "/":
        parent_path = "/".join(current_path.rstrip('/').split('/')[:-1])
        if not parent_path:
            parent_path = "/"
        keyboard.add(InlineKeyboardButton("📁 .. (Наверх)", callback_data=f"folder_{parent_path}"))
    
    for folder in folders:
        folder_name = folder['name']
        folder_path = folder['path'].replace('disk:', '')
        keyboard.add(InlineKeyboardButton(f"📁 {folder_name}", callback_data=f"folder_{folder_path}"))
    
    keyboard.add(InlineKeyboardButton("➕ Создать новую папку", callback_data="create_new_folder"))
    keyboard.add(InlineKeyboardButton("✅ Выбрать текущую папку", callback_data=f"select_current_folder_{current_path}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_folder_selection"))
    
    await bot.send_message(
        chat_id,
        f"📁 **Выберите папку для бэкапов**\n\n"
        f"📍 Текущий путь: `{current_path}`\n\n"
        f"Выберите папку из списка или создайте новую:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


@dp.callback_query_handler(lambda c: c.data.startswith("folder_"))
async def navigate_folder(callback: types.CallbackQuery, state: FSMContext):
    folder_path = callback.data.replace("folder_", "")
    await show_folders(callback.from_user.id, folder_path, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "create_new_folder")
async def create_new_folder_prompt(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(
        callback.from_user.id,
        "📁 **Введите название новой папки:**\n\n"
        "⏰ **У вас есть 3 минуты**\n\n"
        "💡 Для отмены отправьте /cancel",
        parse_mode='Markdown'
    )
    await SettingsStates.waiting_for_new_folder_name.set()
    await callback.answer()


@dp.message_handler(state=SettingsStates.waiting_for_new_folder_name)
async def create_new_folder(message: types.Message, state: FSMContext):
    folder_name = message.text.strip()
    user_id = message.from_user.id
    
    if not folder_name:
        await message.reply("❌ **Ошибка!** Введите название папки.", parse_mode='Markdown')
        return
    
    data = await state.get_data()
    current_path = data.get('current_folder', '/')
    
    if current_path == "/":
        new_path = f"/{folder_name}"
    else:
        new_path = f"{current_path}/{folder_name}"
    
    token = get_user_token(user_id)
    if not token:
        await message.reply("❌ Нет доступа к Яндекс.Диску", parse_mode='Markdown')
        await state.finish()
        return
    
    yandex_disk = YandexDiskAPI(token)
    if yandex_disk.create_folder(new_path):
        await message.reply(f"✅ **Папка создана:** `{new_path}`", parse_mode='Markdown')
        await show_folders(user_id, current_path, state)
    else:
        await message.reply(f"❌ **Ошибка создания папки!** Проверьте права доступа.", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data.startswith("select_current_folder_"))
async def select_current_folder(callback: types.CallbackQuery, state: FSMContext):
    folder_path = callback.data.replace("select_current_folder_", "")
    config['backup_path'] = folder_path
    save_data()
    
    await bot.send_message(
        callback.from_user.id,
        f"✅ **Папка для бэкапов установлена:** `{folder_path}`",
        parse_mode='Markdown'
    )
    
    token = get_user_token(callback.from_user.id)
    if token:
        yandex_disk = YandexDiskAPI(token)
        yandex_disk.create_folder(folder_path)
    
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_folder_selection")
async def cancel_folder_selection(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "set_timezone")
async def set_timezone(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    for name in TIMEZONES.keys():
        keyboard.add(InlineKeyboardButton(name, callback_data=f"tz_{name}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_tz"))
    
    await bot.send_message(
        callback.from_user.id,
        "🌍 **Выберите часовой пояс:**\n\n"
        "Текущий: " + config.get('timezone', 'Europe/Moscow'),
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
        f"✅ **Часовой пояс установлен:** {tz_name}\n"
        f"🕐 Текущее время: {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}",
        parse_mode='Markdown'
    )
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_tz")
async def cancel_tz(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "restore_backup")
async def restore_backup_menu(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("☁️ Из Яндекс.Диска", callback_data="restore_from_yadisk"),
        InlineKeyboardButton("📱 Из телефона", callback_data="restore_from_phone"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_restore")
    )
    
    await bot.send_message(
        callback.from_user.id,
        "📤 **Восстановление из бэкапа**\n\n"
        "Выберите источник для восстановления:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "restore_from_phone")
async def restore_from_phone(callback: types.CallbackQuery):
    await send_with_auto_delete(
        callback.from_user.id,
        "📱 **Отправьте JSON файл бэкапа**\n\n"
        "Файл должен быть в формате JSON, созданный этим ботом.\n\n"
        "⏰ **У вас есть 3 минуты** на отправку файла",
        delay=180
    )
    await SettingsStates.waiting_for_upload_backup.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "restore_from_yadisk")
async def restore_from_yadisk(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    token = get_user_token(user_id)
    
    if not token:
        await bot.send_message(
            callback.from_user.id,
            "❌ **Нет доступа к Яндекс.Диску!**\n\n"
            "Сначала авторизуйтесь в настройках.",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    backups = await get_yadisk_backups(user_id)
    
    if not backups:
        await bot.send_message(
            callback.from_user.id,
            "📭 **Нет доступных бэкапов на Яндекс.Диске**\n\n"
            "Сначала создайте бэкап через меню.",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for backup in backups:
        backup_time = backup['name'].replace('backup_', '').replace('.json', '')
        try:
            backup_date = datetime.strptime(backup_time, '%Y%m%d_%H%M%S')
            button_text = f"📦 {backup_date.strftime('%d.%m.%Y %H:%M:%S')}"
        except:
            button_text = f"📦 {backup['name']}"
        keyboard.add(InlineKeyboardButton(button_text, callback_data=f"select_backup_{backup['name']}"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_restore"))
    
    await bot.send_message(
        callback.from_user.id,
        "📦 **Выберите бэкап для восстановления:**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("select_backup_"))
async def select_backup(callback: types.CallbackQuery, state: FSMContext):
    backup_name = callback.data.replace("select_backup_", "")
    user_id = callback.from_user.id
    
    status_msg = await bot.send_message(
        callback.from_user.id,
        "⏳ **Восстановление из бэкапа...**",
        parse_mode='Markdown'
    )
    
    if await restore_from_yadisk_backup(backup_name, user_id):
        await status_msg.edit_text(
            "✅ **Данные успешно восстановлены из бэкапа!**\n\n"
            f"📝 Уведомлений: {len(notifications)}",
            parse_mode='Markdown'
        )
    else:
        await status_msg.edit_text(
            "❌ **Ошибка восстановления!**\n\n"
            "Не удалось восстановить данные из бэкапа.",
            parse_mode='Markdown'
        )
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_restore")
async def cancel_restore(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.message_handler(content_types=['document'], state=SettingsStates.waiting_for_upload_backup)
async def receive_backup_file(message: types.Message, state: FSMContext):
    global notifications, config
    
    try:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file.file_path)
        backup_data = json.loads(downloaded_file.read().decode('utf-8'))
        
        if 'notifications' in backup_data:
            notifications = backup_data['notifications']
            if 'config' in backup_data:
                config = backup_data['config']
            save_data()
            await message.reply(
                f"✅ **Данные восстановлены!**\n\n"
                f"📝 Уведомлений: {len(notifications)}",
                parse_mode='Markdown'
            )
        else:
            await message.reply("❌ **Неверный формат бэкапа!**", parse_mode='Markdown')
    except Exception as e:
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery, state: FSMContext):
    global notifications_enabled
    notifications_enabled = not notifications_enabled
    config['notifications_enabled'] = notifications_enabled
    save_data()
    
    status = "включены" if notifications_enabled else "выключены"
    await bot.send_message(
        callback.from_user.id,
        f"✅ **Уведомления {status}!**",
        parse_mode='Markdown'
    )
    await settings_menu(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "set_max_backups")
async def set_max_backups(callback: types.CallbackQuery):
    await send_with_auto_delete(
        callback.from_user.id,
        f"📊 **Текущее количество:** `{config.get('max_backups', 5)}`\n\n"
        "**Введите число (1-20):**\n\n⏰ **У вас есть 3 минуты**",
        delay=180
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
            await message.reply(f"✅ **Установлено:** `{max_backups}`", parse_mode='Markdown')
        else:
            await message.reply("❌ **Ошибка!** Число от 1 до 20", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите число", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "set_check_time")
async def set_check_time(callback: types.CallbackQuery):
    await send_with_auto_delete(
        callback.from_user.id,
        f"🕐 **Текущее время:** `{config.get('daily_check_time', '06:00')}`\n\n"
        "**Введите время (ЧЧ:ММ):**\n\n⏰ **У вас есть 3 минуты**",
        delay=180
    )
    await SettingsStates.waiting_for_check_time.set()
    await callback.answer()


@dp.message_handler(state=SettingsStates.waiting_for_check_time)
async def save_check_time(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%H:%M")
        config['daily_check_time'] = message.text
        save_data()
        await message.reply(f"✅ **Время установлено:** `{message.text}`", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Ошибка!** Формат `ЧЧ:ММ`", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    access = False
    access_message = "Не авторизован"
    
    if ADMIN_ID:
        access, access_message = await check_yandex_access(ADMIN_ID)
    
    info = f"""
📊 **СТАТИСТИКА**

🤖 **Версия:** v{BOT_VERSION} ({BOT_VERSION_DATE})

📝 **Уведомлений:** `{len(notifications)}`
💾 **Максимум бэкапов:** `{config.get('max_backups', 5)}`
📁 **Путь бэкапов:** `{config['backup_path']}`
🕐 **Проверка:** `{config.get('daily_check_time', '06:00')}`
🌍 **Часовой пояс:** `{config.get('timezone', 'Europe/Moscow')}`
🕐 **Текущее время:** `{get_current_time().strftime('%d.%m.%Y %H:%M:%S')}`
🔔 **Уведомления:** `{'Вкл' if notifications_enabled else 'Выкл'}`

🔑 **Яндекс.Диск:** `{'✅ Доступен' if access else '❌ ' + access_message}`
"""
    await bot.send_message(callback.from_user.id, info, parse_mode='Markdown')
    await callback.answer()


@dp.message_handler(commands=['restart'])
async def restart_bot(message: types.Message):
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        await message.reply("🔄 **Перезапуск...**", parse_mode='Markdown')
        await asyncio.sleep(2)
        os._exit(0)


@dp.message_handler(commands=['version'])
async def show_version(message: types.Message):
    await message.reply(
        f"🤖 **Бот для уведомлений**\n"
        f"📌 **Версия:** v{BOT_VERSION}\n"
        f"📅 **Дата:** {BOT_VERSION_DATE}",
        parse_mode='Markdown'
    )


@dp.message_handler(commands=['cancel'], state='*')
async def cancel_operation(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.reply("❌ **Нет активных операций для отмены**", parse_mode='Markdown')
        return
    
    await state.finish()
    await message.reply("✅ **Операция отменена!**", parse_mode='Markdown')
    await cmd_start(message, state)


async def on_startup(dp):
    init_folders()
    load_data()
    
    new_notifications = {}
    for i, (notif_id, notif) in enumerate(notifications.items(), 1):
        notif['num'] = i
        new_notifications[str(i)] = notif
    notifications.clear()
    notifications.update(new_notifications)
    save_data()
    
    print(f"\n{'='*50}")
    print(f"🤖 БОТ ДЛЯ УВЕДОМЛЕНИЙ v{BOT_VERSION} ({BOT_VERSION_DATE})")
    print(f"{'='*50}")
    
    if ADMIN_ID and get_user_token(ADMIN_ID):
        access, message = await check_yandex_access(ADMIN_ID)
        if access:
            print("✅ Доступ к Яндекс.Диску получен")
        else:
            print(f"⚠️ Токен есть, но доступ ограничен: {message}")
    else:
        print("❌ Нет токена Яндекс.Диска (требуется авторизация)")
    
    print(f"📝 Загружено уведомлений: {len(notifications)}")
    print(f"🔔 Уведомления: {'Включены' if notifications_enabled else 'Выключены'}")
    print(f"🌍 Часовой пояс: {config.get('timezone', 'Europe/Moscow')}")
    print(f"🕐 Текущее время: {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"{'='*50}\n")
    
    asyncio.create_task(check_notifications())
    asyncio.create_task(daily_check())
    print("✅ Бот успешно запущен!")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)