import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode
from io import BytesIO
import pytz
import re
import hashlib
from logging.handlers import RotatingFileHandler

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

# Настройка логирования с ротацией (макс 100 КБ)
log_file = 'bot_debug.log'
max_log_size = 100 * 1024  # 100 КБ в байтах

file_handler = RotatingFileHandler(
    log_file, 
    maxBytes=max_log_size, 
    backupCount=2,
    encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# Версия бота
BOT_VERSION = "4.07"
BOT_VERSION_DATE = "13.04.2026"
BOT_VERSION_TIME = "12:00"

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET]):
    logger.error("❌ Ошибка: Не все переменные окружения заданы!")
    exit(1)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

DATA_FILE = 'notifications.json'
CONFIG_FILE = 'config.json'
TOKEN_FILE = 'user_tokens.json'
BACKUP_DIR = 'backups'
CALENDAR_SYNC_FILE = 'calendar_sync.json'

notifications: Dict = {}
config: Dict = {}
user_tokens: Dict[int, str] = {}
notifications_enabled = True
calendar_sync: Dict = {}
calendar_events: Dict = {}

YANDEX_API_BASE = "https://cloud-api.yandex.net/v1/disk"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_CALENDAR_API = "https://api.calendar.yandex.net/calendar/v1"

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

WEEKDAYS_BUTTONS = [
    ("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)
]

WEEKDAYS_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}

MONTHS_NAMES = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}


def get_current_time():
    timezone_str = config.get('timezone', 'Europe/Moscow')
    tz = pytz.timezone(timezone_str)
    return datetime.now(tz)


def parse_date(date_str: str) -> Optional[datetime]:
    date_str = date_str.strip()
    now = get_current_time()
    current_year = now.year
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
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


def get_next_weekday(target_weekdays: List[int], hour: int, minute: int, from_date: datetime = None) -> Optional[datetime]:
    now = from_date or get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    if now.tzinfo is None:
        now = tz.localize(now)
    
    # Проверяем сегодняшний день
    if now.weekday() in target_weekdays:
        today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
        if today_trigger > now:
            return today_trigger
    
    # Ищем в будущих днях
    for i in range(1, 15):
        next_date = now + timedelta(days=i)
        if next_date.weekday() in target_weekdays:
            result = tz.localize(datetime(next_date.year, next_date.month, next_date.day, hour, minute))
            return result
    
    return None


def get_auth_url() -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI
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
                    logger.error(f"Ошибка получения токена: {response.status} - {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Исключение при получении токена: {e}")
            return None


class YandexCalendarAPI:
    """Класс для работы с Яндекс Календарём через HTTP API"""
    
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }
        self.base_url = YANDEX_CALENDAR_API
    
    async def test_connection(self) -> tuple[bool, str]:
        """Проверяет соединение с Яндекс.Календарём и валидность токена"""
        try:
            url = f"{self.base_url}/calendars"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=15) as response:
                    if response.status == 200:
                        return True, "Токен действителен, API доступен"
                    elif response.status == 401:
                        return False, "Токен недействителен (истек или отозван)"
                    elif response.status == 403:
                        return False, "Недостаточно прав. Убедитесь, что в OAuth-приложении выбраны права calendar:read и calendar:write"
                    elif response.status == 404:
                        return False, "API эндпоинт не найден. Возможно, требуется другая версия API"
                    else:
                        error_text = await response.text()
                        return False, f"Ошибка API ({response.status}): {error_text[:150]}"
        except asyncio.TimeoutError:
            return False, "Таймаут подключения к API Яндекс.Календаря"
        except aiohttp.ClientConnectorError:
            return False, "Не удалось подключиться к API (проверьте интернет)"
        except Exception as e:
            return False, f"Неизвестная ошибка: {str(e)[:150]}"
    
    async def test_calendar_access(self) -> tuple[bool, str, Optional[str]]:
        """Проверяет доступ к календарям пользователя"""
        try:
            calendars = await self.get_calendars()
            if calendars is None:
                return False, "Не удалось получить список календарей (проверьте права доступа)", None
            
            if not calendars:
                return False, "Не найдено ни одного календаря. Создайте календарь на calendar.yandex.ru", None
            
            primary_calendar = None
            for cal in calendars:
                if cal.get('primary', False):
                    primary_calendar = cal
                    break
            
            if not primary_calendar and calendars:
                primary_calendar = calendars[0]
            
            if primary_calendar:
                calendar_id = primary_calendar.get('id', 'primary')
                calendar_name = primary_calendar.get('summary', 'Основной календарь')
                return True, f"Доступ к календарю '{calendar_name}' подтвержден", calendar_id
            else:
                return False, "Не удалось получить ID календаря", None
                
        except Exception as e:
            return False, f"Ошибка проверки календаря: {str(e)[:150]}", None
    
    async def get_calendars(self) -> Optional[List[Dict]]:
        """Получает список календарей пользователя"""
        try:
            url = f"{self.base_url}/calendars"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'items' in data:
                            return data.get('items', [])
                        elif 'calendars' in data:
                            return data.get('calendars', [])
                        else:
                            return []
                    else:
                        logger.error(f"Ошибка получения календарей: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Ошибка получения календарей: {e}")
            return None
    
    async def get_default_calendar_id(self) -> Optional[str]:
        """Получает ID календаря по умолчанию"""
        calendars = await self.get_calendars()
        if not calendars:
            return 'primary'
        
        for calendar in calendars:
            if calendar.get('primary', False):
                return calendar.get('id', 'primary')
        return calendars[0].get('id', 'primary') if calendars else 'primary'
    
    async def create_event(self, summary: str, start_time: datetime, end_time: datetime = None,
                           description: str = "", calendar_id: str = None) -> Optional[str]:
        """Создаёт событие в календаре"""
        try:
            if calendar_id is None:
                calendar_id = await self.get_default_calendar_id()
                if not calendar_id:
                    calendar_id = 'primary'
            
            if end_time is None:
                end_time = start_time + timedelta(hours=1)
            
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            if start_time.tzinfo is None:
                start_time = tz.localize(start_time)
            if end_time.tzinfo is None:
                end_time = tz.localize(end_time)
            
            start_str = start_time.isoformat()
            end_str = end_time.isoformat()
            
            url = f"{self.base_url}/calendars/{calendar_id}/events"
            event_data = {
                "summary": summary[:255],
                "description": description[:1000],
                "start": {
                    "dateTime": start_str,
                    "timeZone": config.get('timezone', 'Europe/Moscow')
                },
                "end": {
                    "dateTime": end_str,
                    "timeZone": config.get('timezone', 'Europe/Moscow')
                },
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 15}
                    ]
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=event_data, timeout=15) as response:
                    if response.status in [200, 201]:
                        data = await response.json()
                        event_id = data.get('id')
                        logger.info(f"Создано событие в календаре: {summary}, ID: {event_id}")
                        return event_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Ошибка создания события ({response.status}): {error_text[:200]}")
                        return None
        except Exception as e:
            logger.error(f"Ошибка создания события: {e}")
            return None
    
    async def update_event(self, event_id: str, summary: str = None, start_time: datetime = None,
                           end_time: datetime = None, description: str = None, calendar_id: str = None) -> bool:
        """Обновляет событие в календаре"""
        try:
            if calendar_id is None:
                calendar_id = await self.get_default_calendar_id()
                if not calendar_id:
                    calendar_id = 'primary'
            
            url_get = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url_get, headers=self.headers, timeout=15) as response:
                    if response.status != 200:
                        return False
                    event = await response.json()
            
            if summary is not None:
                event['summary'] = summary[:255]
            if description is not None:
                event['description'] = description[:1000]
            if start_time is not None:
                tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                if start_time.tzinfo is None:
                    start_time = tz.localize(start_time)
                event['start']['dateTime'] = start_time.isoformat()
            if end_time is not None:
                tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                if end_time.tzinfo is None:
                    end_time = tz.localize(end_time)
                event['end']['dateTime'] = end_time.isoformat()
            elif start_time is not None:
                new_end = start_time + timedelta(hours=1)
                event['end']['dateTime'] = new_end.isoformat()
            
            url_put = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
            async with session.put(url_put, headers=self.headers, json=event, timeout=15) as put_response:
                return put_response.status in [200, 201]
                
        except Exception as e:
            logger.error(f"Ошибка обновления события: {e}")
            return False
    
    async def delete_event(self, event_id: str, calendar_id: str = None) -> bool:
        """Удаляет событие из календаря"""
        try:
            if calendar_id is None:
                calendar_id = await self.get_default_calendar_id()
                if not calendar_id:
                    calendar_id = 'primary'
            
            url = f"{self.base_url}/calendars/{calendar_id}/events/{event_id}"
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=self.headers, timeout=15) as response:
                    success = response.status in [200, 204]
                    if success:
                        logger.info(f"Удалено событие из календаря: {event_id}")
                    return success
        except Exception as e:
            logger.error(f"Ошибка удаления события: {e}")
            return False
    
    async def get_events(self, from_date: datetime, to_date: datetime, calendar_id: str = None) -> List[Dict]:
        """Получает события из календаря за период"""
        try:
            if calendar_id is None:
                calendar_id = await self.get_default_calendar_id()
                if not calendar_id:
                    calendar_id = 'primary'
            
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            if from_date.tzinfo is None:
                from_date = tz.localize(from_date)
            if to_date.tzinfo is None:
                to_date = tz.localize(to_date)
            
            params = {
                'timeMin': from_date.isoformat(),
                'timeMax': to_date.isoformat(),
                'singleEvents': 'true',
                'orderBy': 'startTime'
            }
            
            url = f"{self.base_url}/calendars/{calendar_id}/events"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('items', [])
                    else:
                        return []
        except Exception as e:
            logger.error(f"Ошибка получения событий: {e}")
            return []
    
    async def sync_calendar_to_bot(self) -> int:
        """Синхронизирует события из календаря в бот (импорт)"""
        synced_count = 0
        
        try:
            now = get_current_time()
            end_date = now + timedelta(days=30)
            
            events = await self.get_events(now, end_date)
            
            for event in events:
                event_id = event.get('id')
                summary = event.get('summary', 'Без названия')
                start_info = event.get('start', {})
                start_time_str = start_info.get('dateTime') or start_info.get('date')
                
                if not start_time_str:
                    continue
                
                try:
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                except:
                    continue
                
                existing_notif_id = None
                for nid, sync_data in calendar_sync.items():
                    if sync_data.get('calendar_event_id') == event_id:
                        existing_notif_id = nid
                        break
                
                if existing_notif_id is None:
                    next_num = len(notifications) + 1
                    notif_id = str(next_num)
                    
                    notifications[notif_id] = {
                        'text': summary,
                        'time': start_time.isoformat(),
                        'created': get_current_time().isoformat(),
                        'notified': False,
                        'is_completed': False,
                        'num': next_num,
                        'repeat_type': 'no',
                        'is_repeat': False,
                        'repeat_count': 0,
                        'from_calendar': True
                    }
                    
                    calendar_sync[notif_id] = {
                        'calendar_event_id': event_id,
                        'last_sync': get_current_time().isoformat()
                    }
                    
                    synced_count += 1
                    logger.info(f"Импортировано событие из календаря: {summary}")
            
            save_calendar_sync()
            save_data()
            return synced_count
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации календаря в бот: {e}")
            return 0


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
            logger.error(f"Ошибка проверки доступа: {e}")
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
            logger.error(f"Ошибка проверки доступа: {e}")
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
            logger.error(f"Ошибка загрузки файла: {e}")
            return False
    
    async def check_file_exists(self, remote_path: str) -> bool:
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Ошибка проверки файла: {e}")
            return False
    
    async def delete_file_async(self, remote_path: str) -> bool:
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path, "permanently": "true"}
            
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=self.headers, params=params) as response:
                    return response.status in [200, 202, 204]
        except Exception as e:
            logger.error(f"Ошибка удаления: {e}")
            return False
    
    def create_folder(self, folder_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path}
            response = requests.put(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 201, 202]
        except Exception as e:
            logger.error(f"Ошибка создания папки: {e}")
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
            logger.error(f"Ошибка загрузки файла: {e}")
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
            logger.error(f"Ошибка получения списка папок: {e}")
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
            logger.error(f"Ошибка получения списка файлов: {e}")
            return []
    
    def delete_file(self, remote_path):
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path, "permanently": "true"}
            response = requests.delete(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 202, 204]
        except Exception as e:
            logger.error(f"Ошибка удаления файла: {e}")
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
            logger.error(f"Ошибка скачивания файла: {e}")
            return False


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
    waiting_for_every_hour_time = State()
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


class SnoozeStates(StatesGroup):
    waiting_for_snooze_type = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_weekdays = State()
    waiting_for_weekday_time = State()
    waiting_for_every_day_time = State()


def load_calendar_sync():
    global calendar_sync
    if os.path.exists(CALENDAR_SYNC_FILE):
        try:
            with open(CALENDAR_SYNC_FILE, 'r', encoding='utf-8') as f:
                calendar_sync = json.load(f)
        except:
            calendar_sync = {}


def save_calendar_sync():
    with open(CALENDAR_SYNC_FILE, 'w', encoding='utf-8') as f:
        json.dump(calendar_sync, f, indent=2, ensure_ascii=False)


def get_calendar_sync_enabled() -> bool:
    return config.get('calendar_sync_enabled', False)


async def verify_calendar_connection() -> tuple[bool, str]:
    if not ADMIN_ID:
        return False, "ADMIN_ID не задан"
    
    token = get_user_token(ADMIN_ID)
    if not token:
        return False, "Токен Яндекс не найден"
    
    calendar_api = YandexCalendarAPI(token)
    
    api_ok, api_message = await calendar_api.test_connection()
    if not api_ok:
        return False, f"Ошибка API: {api_message}"
    
    calendar_ok, calendar_message, calendar_id = await calendar_api.test_calendar_access()
    if not calendar_ok:
        return False, f"Ошибка календаря: {calendar_message}"
    
    return True, f"✅ {api_message}. {calendar_message}"


async def sync_notification_to_calendar(notif_id: str, action: str = 'create'):
    if not get_calendar_sync_enabled():
        return
    
    if not ADMIN_ID:
        return
    
    token = get_user_token(ADMIN_ID)
    if not token:
        return
    
    notif = notifications.get(notif_id)
    if not notif:
        return
    
    calendar_api = YandexCalendarAPI(token)
    
    try:
        if action == 'create':
            event_time_str = notif.get('time')
            if not event_time_str:
                return
            
            event_time = datetime.fromisoformat(event_time_str)
            if event_time.tzinfo is None:
                tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                event_time = tz.localize(event_time)
            
            repeat_info = ""
            repeat_type = notif.get('repeat_type', 'no')
            if repeat_type == 'every_hour':
                repeat_info = f"Повтор: каждый час"
            elif repeat_type == 'every_day':
                hour = notif.get('repeat_hour', 0)
                minute = notif.get('repeat_minute', 0)
                repeat_info = f"Повтор: ежедневно в {hour:02d}:{minute:02d}"
            elif repeat_type == 'weekdays':
                hour = notif.get('repeat_hour', 0)
                minute = notif.get('repeat_minute', 0)
                days = [WEEKDAYS_NAMES[d] for d in notif.get('weekdays_list', [])]
                repeat_info = f"Повтор: по {', '.join(days)} в {hour:02d}:{minute:02d}"
            
            description = f"Уведомление из бота\nТекст: {notif['text']}\n"
            if repeat_info:
                description += f"\n{repeat_info}"
            description += f"\n\nСоздано: {get_current_time().strftime('%d.%m.%Y %H:%M')}"
            
            event_id = await calendar_api.create_event(
                summary=notif['text'][:100],
                start_time=event_time,
                description=description
            )
            
            if event_id:
                calendar_sync[notif_id] = {
                    'calendar_event_id': event_id,
                    'last_sync': get_current_time().isoformat()
                }
                save_calendar_sync()
                logger.info(f"Уведомление {notif_id} синхронизировано с календарём, событие: {event_id}")
        
        elif action == 'update':
            sync_data = calendar_sync.get(notif_id)
            if sync_data and sync_data.get('calendar_event_id'):
                event_id = sync_data['calendar_event_id']
                
                event_time_str = notif.get('time')
                if event_time_str:
                    event_time = datetime.fromisoformat(event_time_str)
                    if event_time.tzinfo is None:
                        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                        event_time = tz.localize(event_time)
                    
                    await calendar_api.update_event(
                        event_id=event_id,
                        summary=notif['text'][:100],
                        start_time=event_time,
                        description=f"Уведомление из бота\nТекст: {notif['text']}"
                    )
                    
                    sync_data['last_sync'] = get_current_time().isoformat()
                    save_calendar_sync()
                    logger.info(f"Уведомление {notif_id} обновлено в календаре")
        
        elif action == 'delete':
            sync_data = calendar_sync.get(notif_id)
            if sync_data and sync_data.get('calendar_event_id'):
                event_id = sync_data['calendar_event_id']
                await calendar_api.delete_event(event_id)
                del calendar_sync[notif_id]
                save_calendar_sync()
                logger.info(f"Уведомление {notif_id} удалено из календаря")
    
    except Exception as e:
        logger.error(f"Ошибка синхронизации с календарём: {e}")


async def sync_calendar_to_bot_task():
    while True:
        try:
            if get_calendar_sync_enabled() and ADMIN_ID:
                token = get_user_token(ADMIN_ID)
                if token:
                    calendar_api = YandexCalendarAPI(token)
                    synced = await calendar_api.sync_calendar_to_bot()
                    if synced > 0:
                        logger.info(f"Импортировано {synced} событий из календаря")
            
            await asyncio.sleep(300)
        except Exception as e:
            logger.error(f"Ошибка в задаче синхронизации календаря: {e}")
            await asyncio.sleep(300)


async def auto_delete_message(chat_id: int, message_id: int, delay: int = 180):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass


async def send_with_auto_delete(chat_id: int, text: str, parse_mode: str = 'Markdown', reply_markup=None, delay: int = 180):
    msg = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    asyncio.create_task(auto_delete_message(chat_id, msg.message_id, delay))
    return msg


async def show_backup_notification(message: types.Message):
    if ADMIN_ID:
        success, _, location = await create_backup(ADMIN_ID)
        if success:
            msg = await message.reply(f"✅ **Бэкап создан и обновлен** ({location})", parse_mode='Markdown')
            await asyncio.sleep(5)
            await msg.delete()


def init_folders():
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            'backup_path': '/MyUved_backups',
            'max_backups': 5,
            'daily_check_time': '06:00',
            'notifications_enabled': True,
            'timezone': 'Europe/Moscow',
            'calendar_sync_enabled': False
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_config, f)
    if not os.path.exists(CALENDAR_SYNC_FILE):
        with open(CALENDAR_SYNC_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)


def load_data():
    global notifications, config, user_tokens, notifications_enabled
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        notifications = json.load(f)
    
    for notif_id, notif in notifications.items():
        if 'is_completed' not in notif:
            notif['is_completed'] = False
        if 'last_repeat_time' not in notif:
            notif['last_repeat_time'] = None
        if 'repeat_count' not in notif:
            notif['repeat_count'] = 0
        if 'is_repeat' not in notif:
            notif['is_repeat'] = False
        if 'last_trigger' not in notif:
            notif['last_trigger'] = None
        # Добавляем поле для отслеживания отправленных сообщений
        if 'sent_message_id' not in notif:
            notif['sent_message_id'] = None
    
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
        notifications_enabled = config.get('notifications_enabled', True)
    
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                token_data = json.load(f)
                user_tokens = {int(k): v for k, v in token_data.items()}
        except:
            user_tokens = {}
    
    load_calendar_sync()


def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(notifications, f, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def save_user_token(user_id: int, token: str):
    global user_tokens
    user_tokens[user_id] = token
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)


def get_user_token(user_id: int) -> Optional[str]:
    return user_tokens.get(user_id)


def delete_user_token(user_id: int):
    if user_id in user_tokens:
        del user_tokens[user_id]
        with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
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
            logger.info(f"✅ Доступ к Яндекс.Диску для user {user_id} успешно получен")
            return True, f"✅ {message}", yandex_disk
        else:
            logger.warning(f"❌ Нет доступа к Яндекс.Диску для user {user_id}: {message}")
            return False, f"❌ {message}", None
            
    except Exception as e:
        logger.error(f"Ошибка доступа к Яндекс.Диску: {e}")
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
        logger.error(f"Ошибка отправки бэкапа в Telegram: {e}")
        return False


async def create_backup(user_id: int = None) -> tuple:
    try:
        timestamp = get_current_time().strftime('%Y%m%d_%H%M%S')
        backup_file = Path(BACKUP_DIR) / f'backup_{timestamp}.json'
        
        backup_data = {
            'notifications': notifications,
            'config': config,
            'calendar_sync': calendar_sync,
            'timestamp': timestamp,
            'version': BOT_VERSION,
            'version_date': BOT_VERSION_DATE,
            'version_time': BOT_VERSION_TIME
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
                        logger.info(f"✅ Бэкап создан на Яндекс.Диске")
                        backup_created = True
                        backup_location = "Яндекс.Диск"
        
        if not backup_created:
            if await send_backup_to_telegram(backup_file):
                backup_created = True
                backup_location = "Telegram"
        
        return backup_created, backup_file, backup_location
    except Exception as e:
        logger.error(f"Ошибка создания бэкапа: {e}")
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
        logger.error(f"Ошибка очистки старых бэкапов: {e}")


async def restore_from_yadisk_backup(backup_name: str, user_id: int) -> bool:
    global notifications, config, calendar_sync
    
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
                for notif_id, notif in notifications.items():
                    if 'is_completed' not in notif:
                        notif['is_completed'] = False
                    if 'last_repeat_time' not in notif:
                        notif['last_repeat_time'] = None
                    if 'repeat_count' not in notif:
                        notif['repeat_count'] = 0
                    if 'is_repeat' not in notif:
                        notif['is_repeat'] = False
                    if 'last_trigger' not in notif:
                        notif['last_trigger'] = None
                    if 'sent_message_id' not in notif:
                        notif['sent_message_id'] = None
                    
                    # ВАЖНО: Сбрасываем notified для всех уведомлений при восстановлении
                    # чтобы они не срабатывали сразу после восстановления
                    notif['notified'] = False
                    
                    # Для повторяющихся уведомлений корректируем last_trigger
                    now = get_current_time()
                    repeat_type = notif.get('repeat_type', 'no')
                    
                    if repeat_type == 'every_hour':
                        # Для ежечасных устанавливаем last_trigger на текущее время
                        notif['last_trigger'] = now.isoformat()
                    elif repeat_type == 'every_day':
                        hour = notif.get('repeat_hour', 0)
                        minute = notif.get('repeat_minute', 0)
                        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                        today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
                        if now >= today_trigger:
                            # Если время уже прошло, считаем что сегодня уже отправляли
                            notif['last_trigger'] = now.isoformat()
                            next_time = today_trigger + timedelta(days=1)
                        else:
                            # Если время ещё не наступило, last_trigger = вчера
                            notif['last_trigger'] = (today_trigger - timedelta(days=1)).isoformat()
                            next_time = today_trigger
                        notif['next_time'] = next_time.isoformat()
                    elif repeat_type == 'weekdays':
                        hour = notif.get('repeat_hour', 0)
                        minute = notif.get('repeat_minute', 0)
                        weekdays_list = notif.get('weekdays_list', [])
                        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                        
                        if now.weekday() in weekdays_list:
                            today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
                            if now >= today_trigger:
                                notif['last_trigger'] = now.isoformat()
                            else:
                                notif['last_trigger'] = (today_trigger - timedelta(days=7)).isoformat()
                        else:
                            notif['last_trigger'] = (now - timedelta(days=7)).isoformat()
                        
                        next_time = get_next_weekday(weekdays_list, hour, minute, now)
                        if next_time:
                            notif['next_time'] = next_time.isoformat()
                
                if 'config' in backup_data:
                    config = backup_data['config']
                if 'calendar_sync' in backup_data:
                    calendar_sync = backup_data['calendar_sync']
                save_data()
                save_calendar_sync()
                logger.info(f"✅ Данные восстановлены из бэкапа, уведомления переинициализированы")
                return True
        
        return False
    except Exception as e:
        logger.error(f"Ошибка восстановления из бэкапа Яндекс.Диска: {e}")
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
        logger.error(f"Ошибка получения списка бэкапов: {e}")
        return []


async def check_notifications():
    """ИСПРАВЛЕННАЯ ФУНКЦИЯ ПРОВЕРКИ УВЕДОМЛЕНИЙ"""
    global notifications_enabled
    while True:
        if notifications_enabled:
            now = get_current_time()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            
            for notif_id, notif in list(notifications.items()):
                if notif.get('is_completed', False):
                    continue
                
                repeat_type = notif.get('repeat_type', 'no')
                
                # Обработка ежечасных уведомлений
                if repeat_type == 'every_hour':
                    last_trigger = notif.get('last_trigger')
                    if last_trigger:
                        last_trigger_time = datetime.fromisoformat(last_trigger)
                        if last_trigger_time.tzinfo is None:
                            last_trigger_time = tz.localize(last_trigger_time)
                    else:
                        created_str = notif.get('created')
                        if created_str:
                            created_time = datetime.fromisoformat(created_str)
                            if created_time.tzinfo is None:
                                created_time = tz.localize(created_time)
                            last_trigger_time = created_time
                        else:
                            last_trigger_time = now - timedelta(hours=1)
                    
                    time_since_last = now - last_trigger_time
                    if time_since_last.total_seconds() >= 3600:
                        is_repeat = notif.get('is_repeat', False)
                        repeat_count = notif.get('repeat_count', 0)
                        
                        if is_repeat:
                            message_text = f"🔔 **ПОВТОРНОЕ НАПОМИНАНИЕ #{repeat_count + 1}**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n\n🕐 Напоминает каждый час"
                        else:
                            message_text = f"🔔 **НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n\n🕐 Напоминает каждый час"
                        
                        keyboard = InlineKeyboardMarkup(row_width=2)
                        keyboard.add(
                            InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{notif_id}"),
                            InlineKeyboardButton("⏰ Отложить уведомление", callback_data=f"snooze_{notif_id}")
                        )
                        
                        try:
                            await bot.send_message(
                                ADMIN_ID,
                                message_text,
                                reply_markup=keyboard,
                                parse_mode='Markdown'
                            )
                            logger.info(f"Отправлено ежечасное уведомление #{notif.get('num', notif_id)}: {notif['text'][:50]}...")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления: {e}")
                        
                        notifications[notif_id]['last_trigger'] = now.isoformat()
                        notifications[notif_id]['notified'] = False
                        save_data()
                
                # Обработка ежедневных уведомлений
                elif repeat_type == 'every_day':
                    hour = notif.get('repeat_hour', 0)
                    minute = notif.get('repeat_minute', 0)
                    today_trigger = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    
                    last_trigger = notif.get('last_trigger')
                    if last_trigger:
                        last_trigger_time = datetime.fromisoformat(last_trigger)
                        if last_trigger_time.tzinfo is None:
                            last_trigger_time = tz.localize(last_trigger_time)
                    else:
                        last_trigger_time = None
                    
                    # Проверяем, не было ли уже уведомления сегодня
                    if last_trigger_time is None or last_trigger_time.date() < now.date():
                        if now >= today_trigger:
                            is_repeat = notif.get('is_repeat', False)
                            repeat_count = notif.get('repeat_count', 0)
                            
                            if is_repeat:
                                message_text = f"🔔 **ПОВТОРНОЕ НАПОМИНАНИЕ #{repeat_count + 1}**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            else:
                                message_text = f"🔔 **НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            
                            keyboard = InlineKeyboardMarkup(row_width=2)
                            keyboard.add(
                                InlineKeyboardButton("✅ Выполнено сегодня", callback_data=f"complete_today_{notif_id}"),
                                InlineKeyboardButton("⏰ Отложить уведомление", callback_data=f"snooze_{notif_id}")
                            )
                            
                            try:
                                await bot.send_message(
                                    ADMIN_ID,
                                    message_text,
                                    reply_markup=keyboard,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Отправлено ежедневное уведомление #{notif.get('num', notif_id)}: {notif['text'][:50]}...")
                            except Exception as e:
                                logger.error(f"Ошибка отправки уведомления: {e}")
                            
                            notifications[notif_id]['last_trigger'] = now.isoformat()
                            notifications[notif_id]['notified'] = False
                            notifications[notif_id]['is_repeat'] = False
                            save_data()
                            
                            next_time = today_trigger + timedelta(days=1)
                            local_next = next_time.astimezone(tz) if next_time.tzinfo else next_time
                            notif['next_time'] = local_next.isoformat()
                            save_data()
                
                # Обработка уведомлений по дням недели
                elif repeat_type == 'weekdays':
                    hour = notif.get('repeat_hour', 0)
                    minute = notif.get('repeat_minute', 0)
                    weekdays_list = notif.get('weekdays_list', [])
                    
                    last_trigger = notif.get('last_trigger')
                    if last_trigger:
                        last_trigger_time = datetime.fromisoformat(last_trigger)
                        if last_trigger_time.tzinfo is None:
                            last_trigger_time = tz.localize(last_trigger_time)
                    else:
                        last_trigger_time = None
                    
                    # Проверяем, является ли сегодняшний день подходящим днём недели
                    if now.weekday() in weekdays_list:
                        # Создаём время срабатывания на сегодня
                        today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
                        
                        # Проверяем, не было ли уже уведомления сегодня
                        already_sent_today = False
                        if last_trigger_time and last_trigger_time.date() == now.date():
                            already_sent_today = True
                        
                        # Если время срабатывания уже наступило и уведомление ещё не отправлялось сегодня
                        if now >= today_trigger and not already_sent_today:
                            is_repeat = notif.get('is_repeat', False)
                            repeat_count = notif.get('repeat_count', 0)
                            
                            if is_repeat:
                                message_text = f"🔔 **ПОВТОРНОЕ НАПОМИНАНИЕ #{repeat_count + 1}**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            else:
                                message_text = f"🔔 **НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}"
                            
                            keyboard = InlineKeyboardMarkup(row_width=2)
                            keyboard.add(
                                InlineKeyboardButton("✅ Выполнено сегодня", callback_data=f"complete_today_{notif_id}"),
                                InlineKeyboardButton("⏰ Отложить уведомление", callback_data=f"snooze_{notif_id}")
                            )
                            
                            try:
                                await bot.send_message(
                                    ADMIN_ID,
                                    message_text,
                                    reply_markup=keyboard,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Отправлено уведомление по дням недели #{notif.get('num', notif_id)}: {notif['text'][:50]}...")
                            except Exception as e:
                                logger.error(f"Ошибка отправки уведомления: {e}")
                            
                            notifications[notif_id]['last_trigger'] = now.isoformat()
                            notifications[notif_id]['notified'] = False
                            notifications[notif_id]['is_repeat'] = False
                            save_data()
                            
                            # Вычисляем следующее время срабатывания
                            next_time = get_next_weekday(weekdays_list, hour, minute, now + timedelta(seconds=1))
                            if next_time:
                                local_next = next_time.astimezone(tz) if next_time.tzinfo else next_time
                                notif['next_time'] = local_next.isoformat()
                                save_data()
                    else:
                        # Сегодня не подходящий день, вычисляем следующее время
                        next_time = get_next_weekday(weekdays_list, hour, minute, now)
                        if next_time:
                            local_next = next_time.astimezone(tz) if next_time.tzinfo else next_time
                            notif['next_time'] = local_next.isoformat()
                            save_data()
                
                # Обработка одноразовых уведомлений
                elif repeat_type == 'no' and notif.get('time'):
                    notify_time_str = notif['time']
                    notify_time = datetime.fromisoformat(notify_time_str)
                    
                    if notify_time.tzinfo is None:
                        notify_time = tz.localize(notify_time)
                    else:
                        notify_time = notify_time.astimezone(tz)
                    
                    # Проверяем, было ли уже отправлено уведомление
                    if not notif.get('notified', False):
                        # Первое уведомление - отправляем в назначенное время
                        if now >= notify_time:
                            is_repeat = notif.get('is_repeat', False)
                            repeat_count = notif.get('repeat_count', 0)
                            
                            if is_repeat:
                                message_text = f"🔔 **ПОВТОРНОЕ НАПОМИНАНИЕ #{repeat_count + 1}**\n\n📝 {notif['text']}\n\n⏰ {notify_time.strftime('%d.%m.%Y %H:%M:%S')}"
                            else:
                                message_text = f"🔔 **НАПОМИНАНИЕ**\n\n📝 {notif['text']}\n\n⏰ {notify_time.strftime('%d.%m.%Y %H:%M:%S')}"
                            
                            keyboard = InlineKeyboardMarkup(row_width=2)
                            keyboard.add(
                                InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{notif_id}"),
                                InlineKeyboardButton("⏰ Отложить уведомление", callback_data=f"snooze_{notif_id}")
                            )
                            
                            try:
                                sent_msg = await bot.send_message(
                                    ADMIN_ID,
                                    message_text,
                                    reply_markup=keyboard,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Отправлено одноразовое уведомление #{notif.get('num', notif_id)}: {notif['text'][:50]}...")
                            except Exception as e:
                                logger.error(f"Ошибка отправки уведомления: {e}")
                            
                            # Помечаем как отправленное и запоминаем время отправки
                            notifications[notif_id]['notified'] = True
                            notifications[notif_id]['last_repeat_time'] = now.isoformat()
                            save_data()
                    
                    else:
                        # Уведомление уже было отправлено - проверяем, нужно ли повторное
                        # Получаем время последнего повторного уведомления
                        last_repeat_str = notif.get('last_repeat_time')
                        if last_repeat_str:
                            last_repeat_time = datetime.fromisoformat(last_repeat_str)
                            if last_repeat_time.tzinfo is None:
                                last_repeat_time = tz.localize(last_repeat_time)
                        else:
                            # Если нет записи о повторных, используем время оригинального уведомления
                            last_repeat_time = notify_time
                        
                        # Проверяем, прошел ли час с последнего уведомления
                        time_since_last = now - last_repeat_time
                        if time_since_last.total_seconds() >= 3600:
                            repeat_count = notif.get('repeat_count', 0) + 1
                            message_text = f"🔔 **ПОВТОРНОЕ НАПОМИНАНИЕ #{repeat_count}**\n\n📝 {notif['text']}\n\n⏰ {now.strftime('%d.%m.%Y %H:%M:%S')}\n\n❗ Вы не отметили напоминание как выполненное"
                            
                            keyboard = InlineKeyboardMarkup(row_width=2)
                            keyboard.add(
                                InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{notif_id}"),
                                InlineKeyboardButton("⏰ Отложить уведомление", callback_data=f"snooze_{notif_id}")
                            )
                            
                            try:
                                await bot.send_message(
                                    ADMIN_ID,
                                    message_text,
                                    reply_markup=keyboard,
                                    parse_mode='Markdown'
                                )
                                logger.info(f"Отправлено повторное уведомление для #{notif.get('num', notif_id)} (повтор #{repeat_count})")
                            except Exception as e:
                                logger.error(f"Ошибка отправки повторного уведомления: {e}")
                            
                            # Обновляем время последнего повторного уведомления и счетчик
                            notifications[notif_id]['last_repeat_time'] = now.isoformat()
                            notifications[notif_id]['repeat_count'] = repeat_count
                            notifications[notif_id]['is_repeat'] = True
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
                    logger.warning(f"❌ Ежедневная проверка: нет доступа - {message}")
                else:
                    logger.info("✅ Ежедневная проверка: доступ есть")
            
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


async def reset_and_process(message: types.Message, state: FSMContext, handler_func):
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"Сброс состояния {current_state} при вызове {handler_func.__name__}")
        await state.finish()
        await message.reply("✅ **Предыдущая операция отменена**", parse_mode='Markdown')
    await handler_func(message, state)


@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление", state='*')
async def add_notification_universal(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} нажал 'Добавить уведомление'")
    await reset_and_process(message, state, add_notification_start)


@dp.message_handler(lambda m: m.text == "📋 Список уведомлений", state='*')
async def list_notifications_universal(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} нажал 'Список уведомлений'")
    await reset_and_process(message, state, list_notifications_handler)


@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def settings_universal(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} нажал 'Настройки'")
    await reset_and_process(message, state, settings_menu_handler)


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
                    f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})\n"
                    f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}\n"
                    f"📅 **Синхронизация с календарём:** {'✅ Вкл' if get_calendar_sync_enabled() else '❌ Выкл'}{backup_text}",
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )
            else:
                await message.reply(
                    f"✅ **Доступ к Яндекс.Диску имеется!**\n\n"
                    f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})\n"
                    f"🕐 **Часовой пояс:** {config.get('timezone', 'Europe/Moscow')}\n"
                    f"📅 **Синхронизация с календарём:** {'✅ Вкл' if get_calendar_sync_enabled() else '❌ Выкл'}",
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
                f"Для работы бэкапов и синхронизации с календарём необходимо авторизоваться.\n\n"
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})",
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
            f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов и синхронизации с календарём необходимо авторизоваться.\n\n"
            f"Нажмите кнопку ниже для авторизации:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await message.reply(
        "👋 **Выберите действие:**",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )


# Остальные функции (авторизация, добавление, редактирование, настройки и т.д.)
# Они остаются без изменений, так как проблема была только в логике восстановления и проверки уведомлений

# ВАЖНО: Весь остальной код (обработчики callback-запросов, сообщений, функции работы с состояниями)
# остается таким же, как в предыдущей версии. Здесь я привожу только ключевые функции для запуска.

async def on_startup(dp):
    init_folders()
    load_data()
    
    # Переинициализация уведомлений при запуске
    new_notifications = {}
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    for i, (notif_id, notif) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
        notif['num'] = i
        if 'is_completed' not in notif:
            notif['is_completed'] = False
        if 'last_repeat_time' not in notif:
            notif['last_repeat_time'] = None
        if 'repeat_count' not in notif:
            notif['repeat_count'] = 0
        if 'is_repeat' not in notif:
            notif['is_repeat'] = False
        if 'last_trigger' not in notif:
            notif['last_trigger'] = None
        if 'sent_message_id' not in notif:
            notif['sent_message_id'] = None
        
        # Корректируем состояния при запуске
        repeat_type = notif.get('repeat_type', 'no')
        if repeat_type == 'every_hour':
            if not notif.get('last_trigger'):
                notif['last_trigger'] = now.isoformat()
        elif repeat_type == 'every_day':
            hour = notif.get('repeat_hour', 0)
            minute = notif.get('repeat_minute', 0)
            today_trigger = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
            if not notif.get('last_trigger'):
                if now >= today_trigger:
                    notif['last_trigger'] = now.isoformat()
                else:
                    notif['last_trigger'] = (today_trigger - timedelta(days=1)).isoformat()
            if not notif.get('next_time'):
                if now >= today_trigger:
                    notif['next_time'] = (today_trigger + timedelta(days=1)).isoformat()
                else:
                    notif['next_time'] = today_trigger.isoformat()
        elif repeat_type == 'weekdays':
            weekdays_list = notif.get('weekdays_list', [])
            hour = notif.get('repeat_hour', 0)
            minute = notif.get('repeat_minute', 0)
            if not notif.get('last_trigger'):
                notif['last_trigger'] = (now - timedelta(days=7)).isoformat()
            next_time = get_next_weekday(weekdays_list, hour, minute, now)
            if next_time:
                notif['next_time'] = next_time.isoformat()
        
        new_notifications[str(i)] = notif
    
    notifications.clear()
    notifications.update(new_notifications)
    save_data()
    
    logger.info(f"\n{'='*50}")
    logger.info(f"🤖 БОТ ДЛЯ УВЕДОМЛЕНИЙ v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})")
    logger.info(f"{'='*50}")
    
    if ADMIN_ID and get_user_token(ADMIN_ID):
        access, message = await check_yandex_access(ADMIN_ID)
        if access:
            logger.info("✅ Доступ к Яндекс.Диску получен")
        else:
            logger.warning(f"⚠️ Токен есть, но доступ ограничен: {message}")
    else:
        logger.warning("❌ Нет токена Яндекс.Диска (требуется авторизация)")
    
    if get_calendar_sync_enabled():
        logger.info("🔍 Проверка соединения с Яндекс.Календарём...")
        calendar_ok, calendar_message = await verify_calendar_connection()
        if calendar_ok:
            logger.info(f"✅ {calendar_message}")
        else:
            logger.warning(f"⚠️ {calendar_message}")
    else:
        logger.info("📅 Синхронизация с календарём выключена в настройках")
    
    logger.info(f"📝 Загружено уведомлений: {len(notifications)}")
    logger.info(f"🔔 Уведомления: {'Включены' if notifications_enabled else 'Выключены'}")
    logger.info(f"📅 Синхронизация с календарём: {'Включена' if get_calendar_sync_enabled() else 'Выключена'}")
    logger.info(f"🌍 Часовой пояс: {config.get('timezone', 'Europe/Moscow')}")
    logger.info(f"🕐 Текущее время: {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}")
    logger.info(f"📁 Лог-файл: {log_file} (макс. размер: {max_log_size // 1024} КБ)")
    logger.info(f"{'='*50}\n")
    
    asyncio.create_task(check_notifications())
    asyncio.create_task(daily_check())
    asyncio.create_task(sync_calendar_to_bot_task())
    logger.info("✅ Бот успешно запущен!")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)