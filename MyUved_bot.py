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
                        'from_calendar': True,
                        'last_repeat_time': None,
                        'last_trigger': None
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
                
                # ВАЖНОЕ ИСПРАВЛЕНИЕ: Сбрасываем флаги уведомлений при восстановлении
                now = get_current_time()
                tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
                
                for notif_id, notif in notifications.items():
                    # Добавляем недостающие поля
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
                    
                    # СБРАСЫВАЕМ ФЛАГИ УВЕДОМЛЕНИЙ ПРИ ВОССТАНОВЛЕНИИ
                    notif['notified'] = False
                    
                    # Для повторяющихся уведомлений сбрасываем last_trigger
                    repeat_type = notif.get('repeat_type', 'no')
                    if repeat_type in ['every_day', 'weekdays']:
                        # Устанавливаем last_trigger на предыдущий день
                        if notif.get('next_time'):
                            next_time = datetime.fromisoformat(notif['next_time'])
                            if next_time.tzinfo is None:
                                next_time = tz.localize(next_time)
                            if repeat_type == 'every_day':
                                notif['last_trigger'] = (next_time - timedelta(days=1)).isoformat()
                            elif repeat_type == 'weekdays':
                                notif['last_trigger'] = (next_time - timedelta(days=7)).isoformat()
                        else:
                            notif['last_trigger'] = None
                    elif repeat_type == 'every_hour':
                        notif['last_trigger'] = now.isoformat()
                    
                    # Проверяем, не просрочено ли одноразовое уведомление
                    if repeat_type == 'no' and notif.get('time'):
                        notify_time = datetime.fromisoformat(notif['time'])
                        if notify_time.tzinfo is None:
                            notify_time = tz.localize(notify_time)
                        
                        if notify_time < now:
                            # Уведомление просрочено - НЕ отправляем его сразу
                            # Просто помечаем что оно ещё не отправлено
                            notif['notified'] = False
                            notif['last_repeat_time'] = None
                            logger.info(f"Уведомление #{notif.get('num', notif_id)} просрочено, но не отправлено при восстановлении")
                
                if 'config' in backup_data:
                    config = backup_data['config']
                if 'calendar_sync' in backup_data:
                    calendar_sync = backup_data['calendar_sync']
                save_data()
                save_calendar_sync()
                
                logger.info(f"Данные восстановлены из бэкапа, флаги уведомлений сброшены")
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
    """ИСПРАВЛЕННАЯ ФУНКЦИЯ ПРОВЕРКИ УВЕДОМЛЕНИЙ - уведомления по дням недели работают корректно"""
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
                
                # Обработка уведомлений по дням недели (ИСПРАВЛЕНО)
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
                                await bot.send_message(
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


@dp.callback_query_handler(lambda c: c.data == "start_auth")
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback.from_user.id} начал авторизацию")
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
        f"1️⃣ Перейдите по ссылке для авторизации:\n"
        f"🔗 [Нажмите для авторизации]({auth_url})\n\n"
        f"2️⃣ Войдите в аккаунт Яндекс\n"
        f"3️⃣ Разрешите доступ\n"
        f"4️⃣ Скопируйте код из адресной строки (часть после `code=`)\n"
        f"5️⃣ **Отправьте код сюда текстовым сообщением**\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод кода\n\n"
        f"📝 Пример кода: `5j4iyexor5ltn4ym`",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "auth_method_token", state=AuthStates.waiting_for_auth_method)
async def auth_method_token(callback: types.CallbackQuery, state: FSMContext):
    token_url = "https://oauth.yandex.ru/authorize?response_type=token&client_id=" + CLIENT_ID
    
    await bot.send_message(
        callback.from_user.id,
        f"🔓 **Авторизация через токен**\n\n"
        f"1️⃣ Перейдите по ссылке для получения токена:\n"
        f"🔗 [Получить токен]({token_url})\n\n"
        f"2️⃣ Войдите в аккаунт Яндекс\n"
        f"3️⃣ Разрешите доступ\n"
        f"4️⃣ Скопируйте токен из адресной строки (часть после `access_token=`)\n"
        f"5️⃣ **Отправьте токен сюда текстовым сообщением**\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод токена\n\n"
        f"📝 Пример токена: `y0_AgAAAAABX...`",
        parse_mode='Markdown',
        disable_web_page_preview=True
    )
    await AuthStates.waiting_for_direct_token.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "back_to_auth_methods", state=AuthStates.waiting_for_yandex_code)
async def back_to_auth_methods(callback: types.CallbackQuery, state: FSMContext):
    await start_auth(callback, state)
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
        
        calendar_api = YandexCalendarAPI(token)
        calendar_ok, calendar_message, _ = await calendar_api.test_calendar_access()
        
        calendar_status = ""
        if calendar_ok:
            calendar_status = f"\n📅 **Календарь:** ✅ {calendar_message}"
        else:
            calendar_status = f"\n📅 **Календарь:** ❌ {calendar_message}"
        
        result_message = (
            f"✅ **Токен действителен!**\n\n"
            f"📊 **Результаты проверки:**\n"
            f"✅ {access_message}{calendar_status}\n\n"
            f"📁 **Папка для бэкапов:** `{config['backup_path']}`\n\n"
            f"🎉 **Все функции бота будут работать корректно!**\n\n"
            f"💡 **Для синхронизации с календарём** включите соответствующую опцию в настройках."
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
            
            calendar_api = YandexCalendarAPI(token)
            calendar_ok, calendar_message, _ = await calendar_api.test_calendar_access()
            
            calendar_status = ""
            if calendar_ok:
                calendar_status = f"\n📅 **Календарь:** ✅ {calendar_message}"
            else:
                calendar_status = f"\n📅 **Календарь:** ❌ {calendar_message}"
            
            result_message = (
                f"✅ **Авторизация успешна!**\n\n"
                f"📊 **Результаты проверки:**\n"
                f"✅ {access_message}{calendar_status}\n\n"
                f"📁 **Папка для бэкапов:** `{config['backup_path']}`\n\n"
                f"💡 **Для синхронизации с календарём** включите соответствующую опцию в настройках."
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
    for backup in backups[:10]:
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
            f"📝 Уведомлений: {len(notifications)}\n\n"
            f"⚠️ **Важно:** Флаги уведомлений сброшены. Просроченные уведомления не будут отправлены сразу, они будут ждать следующего срабатывания или повторных напоминаний.",
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


async def add_notification_start(message: types.Message, state: FSMContext):
    logger.info(f"Начало добавления уведомления пользователем {message.from_user.id}")
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
    logger.info(f"Получен текст уведомления от {message.from_user.id}: {message.text[:50]}...")
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("🕐 Каждый час", callback_data="time_every_hour"),
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
    logger.info(f"Выбран тип времени: {time_type}")
    
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
    elif time_type == 'every_hour':
        data = await state.get_data()
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            notifications[edit_id]['repeat_type'] = 'every_hour'
            notifications[edit_id]['notified'] = False
            notifications[edit_id]['is_completed'] = False
            notifications[edit_id]['is_repeat'] = False
            notifications[edit_id]['repeat_count'] = 0
            notifications[edit_id]['last_trigger'] = get_current_time().isoformat()
            save_data()
            
            await sync_notification_to_calendar(edit_id, 'update')
            
            await bot.send_message(
                callback.from_user.id,
                f"✅ **Уведомление #{notifications[edit_id].get('num', edit_id)} изменено!**\n"
                f"📝 {notifications[edit_id]['text']}\n"
                f"📅 **Тип:** Каждый час\n"
                f"🕐 Будет напоминать каждый час\n",
                parse_mode='Markdown'
            )
            await show_backup_notification(callback.message)
            await state.finish()
        else:
            next_num = len(notifications) + 1
            notif_id = str(next_num)
            
            now = get_current_time()
            
            notifications[notif_id] = {
                'text': data['text'],
                'time': now.isoformat(),
                'created': now.isoformat(),
                'notified': False,
                'is_completed': False,
                'num': next_num,
                'repeat_type': 'every_hour',
                'last_trigger': now.isoformat(),
                'is_repeat': False,
                'repeat_count': 0,
                'last_repeat_time': None
            }
            
            save_data()
            logger.info(f"Создано ежечасное уведомление #{next_num}")
            
            await sync_notification_to_calendar(notif_id, 'create')
            
            await bot.send_message(
                callback.from_user.id,
                f"✅ **Уведомление #{next_num} создано!**\n"
                f"📝 {data['text']}\n"
                f"📅 **Тип:** Каждый час\n"
                f"🕐 Будет напоминать каждый час\n\n"
                f"ℹ️ Когда уведомление сработает, вы сможете:\n"
                f"• Нажать «✅ Выполнено» - уведомление удалится\n"
                f"• Нажать «⏰ Отложить уведомление» - выбрать новое время для напоминания",
                parse_mode='Markdown'
            )
            
            await show_backup_notification(callback.message)
            await state.finish()
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
            keyboard.add(InlineKeyboardButton(name, callback_data=f"weekday_{day}"))
        keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="weekdays_done"))
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
async def back_to_time_type_from_weekdays(callback: types.CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific"),
        InlineKeyboardButton("🕐 Каждый час", callback_data="time_every_hour"),
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


@dp.callback_query_handler(lambda c: c.data.startswith('weekday_'), state=NotificationStates.waiting_for_weekdays)
async def select_weekday(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.replace('weekday_', ''))
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
        keyboard.add(InlineKeyboardButton(text, callback_data=f"weekday_{d}"))
    keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="weekdays_done"))
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


@dp.callback_query_handler(lambda c: c.data == "weekdays_done", state=NotificationStates.waiting_for_weekdays)
async def weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день недели!")
        return
    
    await state.update_data(weekdays_list=selected)
    logger.info(f"Выбраны дни недели: {selected}")
    
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
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            notifications[edit_id]['repeat_hour'] = hour
            notifications[edit_id]['repeat_minute'] = minute
            notifications[edit_id]['repeat_type'] = 'every_day'
            
            now = get_current_time()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            next_time = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
            if next_time <= now:
                next_time += timedelta(days=1)
            
            notifications[edit_id]['next_time'] = next_time.isoformat()
            notifications[edit_id]['last_trigger'] = (next_time - timedelta(days=1)).isoformat()
            notifications[edit_id]['time'] = next_time.isoformat()
            notifications[edit_id]['notified'] = False
            notifications[edit_id]['is_completed'] = False
            notifications[edit_id]['is_repeat'] = False
            notifications[edit_id]['repeat_count'] = 0
            save_data()
            
            await sync_notification_to_calendar(edit_id, 'update')
            
            await message.reply(
                f"✅ **Уведомление #{notifications[edit_id].get('num', edit_id)} изменено!**\n"
                f"📝 {notifications[edit_id]['text']}\n"
                f"📅 **Тип:** Ежедневное\n"
                f"⏰ **Новое время:** {hour:02d}:{minute:02d}\n",
                parse_mode='Markdown'
            )
            await show_backup_notification(message)
            await state.finish()
        else:
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
                'is_completed': False,
                'num': next_num,
                'repeat_type': 'every_day',
                'repeat_hour': hour,
                'repeat_minute': minute,
                'last_trigger': (first_time - timedelta(days=1)).isoformat(),
                'next_time': first_time.isoformat(),
                'is_repeat': False,
                'repeat_count': 0,
                'last_repeat_time': None
            }
            
            save_data()
            logger.info(f"Создано ежедневное уведомление #{next_num}")
            
            await sync_notification_to_calendar(notif_id, 'create')
            
            await message.reply(
                f"✅ **Уведомление #{next_num} создано!**\n"
                f"📝 {data['text']}\n"
                f"📅 **Тип:** Ежедневное\n"
                f"⏰ **Время:** {hour:02d}:{minute:02d}\n"
                f"🔁 Будет повторяться каждый день\n\n"
                f"ℹ️ Когда уведомление сработает, вы сможете:\n"
                f"• Нажать «✅ Выполнено сегодня» - уведомление не повторится сегодня\n"
                f"• Нажать «⏰ Отложить уведомление» - выбрать новое время для напоминания",
                parse_mode='Markdown'
            )
            
            await show_backup_notification(message)
            await state.finish()
    except Exception as e:
        logger.error(f"Ошибка создания/редактирования ежедневного уведомления: {e}")
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
        edit_id = data.get('edit_id')
        weekdays_list = data.get('weekdays_list', [])
        
        if edit_id and edit_id in notifications:
            notifications[edit_id]['weekdays_list'] = weekdays_list
            notifications[edit_id]['repeat_hour'] = hour
            notifications[edit_id]['repeat_minute'] = minute
            notifications[edit_id]['repeat_type'] = 'weekdays'
            
            now = get_current_time()
            next_time = get_next_weekday(weekdays_list, hour, minute, now)
            if next_time:
                notifications[edit_id]['next_time'] = next_time.isoformat()
                notifications[edit_id]['last_trigger'] = (next_time - timedelta(days=7)).isoformat()
                notifications[edit_id]['time'] = next_time.isoformat()
            
            notifications[edit_id]['notified'] = False
            notifications[edit_id]['is_completed'] = False
            notifications[edit_id]['is_repeat'] = False
            notifications[edit_id]['repeat_count'] = 0
            save_data()
            
            await sync_notification_to_calendar(edit_id, 'update')
            
            days_names = [WEEKDAYS_NAMES[d] for d in sorted(weekdays_list)]
            await message.reply(
                f"✅ **Уведомление #{notifications[edit_id].get('num', edit_id)} изменено!**\n"
                f"📝 {notifications[edit_id]['text']}\n"
                f"📅 **Тип:** По дням недели\n"
                f"📆 **Дни:** {', '.join(days_names)}\n"
                f"⏰ **Новое время:** {hour:02d}:{minute:02d}\n",
                parse_mode='Markdown'
            )
            await show_backup_notification(message)
            await state.finish()
        else:
            if not weekdays_list:
                weekdays_list = data.get('weekdays_list', [])
            
            if not weekdays_list:
                await message.reply("❌ **Ошибка!** Не выбраны дни недели", parse_mode='Markdown')
                return
            
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
                'is_completed': False,
                'num': next_num,
                'repeat_type': 'weekdays',
                'repeat_hour': hour,
                'repeat_minute': minute,
                'weekdays_list': weekdays_list,
                'last_trigger': (first_time - timedelta(days=7)).isoformat(),
                'next_time': first_time.isoformat(),
                'is_repeat': False,
                'repeat_count': 0,
                'last_repeat_time': None
            }
            
            save_data()
            logger.info(f"Создано уведомление по дням недели #{next_num}, дни: {days_names}")
            
            await sync_notification_to_calendar(notif_id, 'create')
            
            await message.reply(
                f"✅ **Уведомление #{next_num} создано!**\n"
                f"📝 {data['text']}\n"
                f"📅 **Тип:** По дням недели\n"
                f"📆 **Дни:** {', '.join(days_names)}\n"
                f"⏰ **Время:** {hour:02d}:{minute:02d}\n"
                f"🔁 Будет повторяться каждую неделю\n\n"
                f"ℹ️ Когда уведомление сработает, вы сможете:\n"
                f"• Нажать «✅ Выполнено сегодня» - уведомление не повторится сегодня\n"
                f"• Нажать «⏰ Отложить уведомление» - выбрать новое время для напоминания",
                parse_mode='Markdown'
            )
            
            await show_backup_notification(message)
            await state.finish()
    except Exception as e:
        logger.error(f"Ошибка создания/редактирования уведомления по дням недели: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


async def save_notification(message: types.Message, state: FSMContext, notify_time: datetime):
    data = await state.get_data()
    next_num = len(notifications) + 1
    notif_id = str(next_num)
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    if notify_time.tzinfo is None:
        notify_time = tz.localize(notify_time)
    
    notifications[notif_id] = {
        'text': data['text'],
        'time': notify_time.isoformat(),
        'created': get_current_time().isoformat(),
        'notified': False,
        'is_completed': False,
        'num': next_num,
        'repeat_type': 'no',
        'is_repeat': False,
        'repeat_count': 0,
        'last_repeat_time': None,
        'last_trigger': None
    }
    
    save_data()
    logger.info(f"Создано одноразовое уведомление #{next_num}")
    
    await sync_notification_to_calendar(notif_id, 'create')
    
    await message.reply(
        f"✅ **Уведомление #{next_num} создано!**\n"
        f"📝 {data['text']}\n"
        f"⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📅 Сработает: {notify_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
        f"ℹ️ Когда уведомление сработает, вы сможете:\n"
        f"• Нажать «✅ Выполнено» - уведомление удалится\n"
        f"• Нажать «⏰ Отложить уведомление» - выбрать новое время для напоминания",
        parse_mode='Markdown'
    )
    
    await show_backup_notification(message)
    await state.finish()


@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число часов.", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            notify_time = get_current_time() + timedelta(hours=hours)
            await save_edited_notification(message, state, edit_id, notify_time)
        else:
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
        
        data = await state.get_data()
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            notify_time = get_current_time() + timedelta(days=days)
            await save_edited_notification(message, state, edit_id, notify_time)
        else:
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
        data = await state.get_data()
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            notify_time = get_current_time() + timedelta(days=days)
            await save_edited_notification(message, state, edit_id, notify_time)
        else:
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
        
        data = await state.get_data()
        edit_id = data.get('edit_id')
        
        if edit_id and edit_id in notifications:
            await save_edited_notification(message, state, edit_id, notify_time)
        else:
            await save_notification(message, state, notify_time)
    except Exception as e:
        logger.error(f"Ошибка парсинга даты: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


async def save_edited_notification(message: types.Message, state: FSMContext, notif_id: str, notify_time: datetime):
    logger.info(f"Сохранение отредактированного уведомления {notif_id}")
    
    if notif_id not in notifications:
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
        await state.finish()
        return
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    if notify_time.tzinfo is None:
        notify_time = tz.localize(notify_time)
    
    notifications[notif_id]['time'] = notify_time.isoformat()
    notifications[notif_id]['notified'] = False
    notifications[notif_id]['is_completed'] = False
    notifications[notif_id]['is_repeat'] = False
    notifications[notif_id]['repeat_count'] = 0
    notifications[notif_id]['repeat_type'] = 'no'
    notifications[notif_id]['last_repeat_time'] = None
    
    for key in ['repeat_hour', 'repeat_minute', 'weekdays_list', 'last_trigger', 'next_time']:
        if key in notifications[notif_id]:
            del notifications[notif_id][key]
    
    save_data()
    
    await sync_notification_to_calendar(notif_id, 'update')
    
    await message.reply(
        f"✅ **Уведомление #{notifications[notif_id].get('num', notif_id)} изменено!**\n"
        f"📝 {notifications[notif_id]['text']}\n"
        f"⏰ Новое время: {notify_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
        f"ℹ️ Уведомление стало одноразовым",
        parse_mode='Markdown'
    )
    
    await show_backup_notification(message)
    await state.finish()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('snooze_'), state='*')
async def snooze_notification_start(callback: types.CallbackQuery, state: FSMContext):
    notif_id = callback.data.replace('snooze_', '')
    logger.info(f"Откладывание уведомления {notif_id} пользователем {callback.from_user.id}")
    
    if notif_id not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    
    repeat_count = notifications[notif_id].get('repeat_count', 0)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ На 1 час", callback_data=f"snooze_select_{notif_id}_1_hours"),
        InlineKeyboardButton("⏰ На 3 часа", callback_data=f"snooze_select_{notif_id}_3_hours"),
        InlineKeyboardButton("⏰ На 6 часов", callback_data=f"snooze_select_{notif_id}_6_hours"),
        InlineKeyboardButton("⏰ На 12 часов", callback_data=f"snooze_select_{notif_id}_12_hours"),
        InlineKeyboardButton("📅 На 1 день", callback_data=f"snooze_select_{notif_id}_1_days"),
        InlineKeyboardButton("📅 На 2 дня", callback_data=f"snooze_select_{notif_id}_2_days"),
        InlineKeyboardButton("📅 На 3 дня", callback_data=f"snooze_select_{notif_id}_3_days"),
        InlineKeyboardButton("📅 На 7 дней", callback_data=f"snooze_select_{notif_id}_7_days"),
        InlineKeyboardButton("🎯 Свой вариант", callback_data=f"snooze_custom_{notif_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_snooze")
    )
    
    await bot.send_message(
        callback.from_user.id,
        f"⏰ **Отложить уведомление**\n\n"
        f"📝 {notifications[notif_id]['text']}\n"
        f"🔄 Повторений: {repeat_count}\n\n"
        f"Выберите новое время для напоминания:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("snooze_select_"), state='*')
async def snooze_time_selected(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.replace("snooze_select_", "").split("_")
    notif_id = parts[0]
    value = int(parts[1])
    unit = parts[2]
    
    logger.info(f"Откладывание уведомления {notif_id} на {value} {unit}")
    
    if notif_id not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    
    repeat_count = notifications[notif_id].get('repeat_count', 0)
    
    now = get_current_time()
    if unit == "hours":
        new_time = now + timedelta(hours=value)
    else:
        new_time = now + timedelta(days=value)
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    if new_time.tzinfo is None:
        new_time = tz.localize(new_time)
    
    notifications[notif_id]['time'] = new_time.isoformat()
    notifications[notif_id]['notified'] = False
    notifications[notif_id]['is_completed'] = False
    notifications[notif_id]['is_repeat'] = True
    notifications[notif_id]['repeat_count'] = repeat_count + 1
    notifications[notif_id]['repeat_type'] = 'no'
    notifications[notif_id]['last_repeat_time'] = None
    
    save_data()
    
    await sync_notification_to_calendar(notif_id, 'update')
    
    logger.info(f"Уведомление {notif_id} отложено на {value} {unit}, повторение #{repeat_count + 1}")
    
    await bot.send_message(
        callback.from_user.id,
        f"⏰ **Уведомление отложено на {value} {unit}**\n\n"
        f"📝 {notifications[notif_id]['text']}\n"
        f"🔄 Повторение #{repeat_count + 1}\n"
        f"🕐 Новое время: {new_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
        f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
        parse_mode='Markdown'
    )
    
    await show_backup_notification(callback.message)
    
    try:
        await bot.delete_message(callback.from_user.id, callback.message.message_id)
    except:
        pass
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("snooze_custom_"), state='*')
async def snooze_custom(callback: types.CallbackQuery, state: FSMContext):
    notif_id = callback.data.replace("snooze_custom_", "")
    logger.info(f"Пользователь {callback.from_user.id} выбрал свой вариант откладывания для {notif_id}")
    
    if notif_id not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    
    await state.update_data(snooze_notif_id=notif_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="snooze_custom_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="snooze_custom_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="snooze_custom_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="snooze_custom_specific"),
        InlineKeyboardButton("📅 Каждый день", callback_data="snooze_custom_every_day"),
        InlineKeyboardButton("📆 По дням недели", callback_data="snooze_custom_weekdays"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_snooze")
    )
    
    await bot.send_message(
        callback.from_user.id,
        "🎯 **Выберите тип откладывания:**\n\n"
        "Вы можете выбрать произвольное время для следующего напоминания:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await SnoozeStates.waiting_for_snooze_type.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("snooze_custom_"), state=SnoozeStates.waiting_for_snooze_type)
async def snooze_custom_type(callback: types.CallbackQuery, state: FSMContext):
    snooze_type = callback.data.replace("snooze_custom_", "")
    await state.update_data(snooze_custom_type=snooze_type)
    
    if snooze_type == 'hours':
        await send_with_auto_delete(
            callback.from_user.id,
            "⌛ **Введите количество часов:**\n\n"
            "📝 Например: `5` или `24`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await SnoozeStates.waiting_for_hours.set()
    elif snooze_type == 'days':
        await send_with_auto_delete(
            callback.from_user.id,
            "📅 **Введите количество дней:**\n\n"
            "📝 Например: `7` или `30`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await SnoozeStates.waiting_for_days.set()
    elif snooze_type == 'months':
        await send_with_auto_delete(
            callback.from_user.id,
            "📆 **Введите количество месяцев:**\n\n"
            "📝 Например: `1` или `6`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await SnoozeStates.waiting_for_months.set()
    elif snooze_type == 'specific':
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
        await SnoozeStates.waiting_for_specific_date.set()
    elif snooze_type == 'every_day':
        await send_with_auto_delete(
            callback.from_user.id,
            "⏰ **Введите время для ежедневного уведомления**\n\n"
            "📝 Формат: `ЧЧ:ММ`\n\n"
            "📝 Примеры: `09:00` или `18:30`\n\n"
            "⏰ **У вас есть 3 минуты**\n\n"
            "💡 Для отмены отправьте /cancel",
            delay=180
        )
        await SnoozeStates.waiting_for_every_day_time.set()
    elif snooze_type == 'weekdays':
        keyboard = InlineKeyboardMarkup(row_width=3)
        for name, day in WEEKDAYS_BUTTONS:
            keyboard.add(InlineKeyboardButton(name, callback_data=f"snooze_weekday_{day}"))
        keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="snooze_weekdays_done"))
        
        await bot.send_message(
            callback.from_user.id,
            "📅 **Выберите дни недели**\n\n"
            "Нажимайте на дни, чтобы выбрать/отменить.\n"
            "Когда закончите, нажмите «✅ Готово»\n\n"
            "⏰ **У вас есть 3 минуты**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.update_data(snooze_selected_weekdays=[])
        await SnoozeStates.waiting_for_weekdays.set()
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("snooze_weekday_"), state=SnoozeStates.waiting_for_weekdays)
async def snooze_select_weekday(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.replace("snooze_weekday_", ""))
    data = await state.get_data()
    selected = data.get('snooze_selected_weekdays', [])
    
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    
    await state.update_data(snooze_selected_weekdays=selected)
    
    keyboard = InlineKeyboardMarkup(row_width=3)
    for name, d in WEEKDAYS_BUTTONS:
        text = f"✅ {name}" if d in selected else name
        keyboard.add(InlineKeyboardButton(text, callback_data=f"snooze_weekday_{d}"))
    keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="snooze_weekdays_done"))
    
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


@dp.callback_query_handler(lambda c: c.data == "snooze_weekdays_done", state=SnoozeStates.waiting_for_weekdays)
async def snooze_weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('snooze_selected_weekdays', [])
    
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день недели!")
        return
    
    await state.update_data(snooze_weekdays_list=selected)
    
    await send_with_auto_delete(
        callback.from_user.id,
        "⏰ **Введите время для уведомления**\n\n"
        "📝 Формат: `ЧЧ:ММ`\n\n"
        "📝 Примеры: `09:00` или `18:30`\n\n"
        "⏰ **У вас есть 3 минуты**\n\n"
        "💡 Для отмены отправьте /cancel",
        delay=180
    )
    await SnoozeStates.waiting_for_weekday_time.set()
    await callback.answer()


@dp.message_handler(state=SnoozeStates.waiting_for_every_day_time)
async def snooze_set_every_day_time(message: types.Message, state: FSMContext):
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
        notif_id = data.get('snooze_notif_id')
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        now = get_current_time()
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        first_time = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
        
        if first_time <= now:
            first_time += timedelta(days=1)
        
        notifications[notif_id]['time'] = first_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        logger.info(f"Уведомление {notif_id} отложено на ежедневное время")
        
        await message.reply(
            f"✅ **Уведомление отложено!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"⏰ Новое время: каждый день в {hour:02d}:{minute:02d}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except Exception as e:
        logger.error(f"Ошибка откладывания уведомления: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


@dp.message_handler(state=SnoozeStates.waiting_for_weekday_time)
async def snooze_set_weekday_time(message: types.Message, state: FSMContext):
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
        notif_id = data.get('snooze_notif_id')
        weekdays_list = data.get('snooze_weekdays_list', [])
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        first_time = get_next_weekday(weekdays_list, hour, minute)
        
        if not first_time:
            await message.reply("❌ **Ошибка!** Не удалось определить дату", parse_mode='Markdown')
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        
        notifications[notif_id]['time'] = first_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        days_names = [WEEKDAYS_NAMES[d] for d in sorted(weekdays_list)]
        logger.info(f"Уведомление {notif_id} отложено на дни: {days_names}")
        
        await message.reply(
            f"✅ **Уведомление отложено!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"📆 Новое время: {', '.join(days_names)} в {hour:02d}:{minute:02d}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except Exception as e:
        logger.error(f"Ошибка откладывания уведомления: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


@dp.message_handler(state=SnoozeStates.waiting_for_hours)
async def snooze_set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число часов.", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notif_id = data.get('snooze_notif_id')
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        new_time = get_current_time() + timedelta(hours=hours)
        
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        if new_time.tzinfo is None:
            new_time = tz.localize(new_time)
        
        notifications[notif_id]['time'] = new_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        logger.info(f"Уведомление {notif_id} отложено на {hours} часов")
        
        await message.reply(
            f"✅ **Уведомление отложено на {hours} час(ов)!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"🕐 Новое время: {new_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число часов.", parse_mode='Markdown')


@dp.message_handler(state=SnoozeStates.waiting_for_days)
async def snooze_set_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        if days <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число дней.", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notif_id = data.get('snooze_notif_id')
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        new_time = get_current_time() + timedelta(days=days)
        
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        if new_time.tzinfo is None:
            new_time = tz.localize(new_time)
        
        notifications[notif_id]['time'] = new_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        logger.info(f"Уведомление {notif_id} отложено на {days} дней")
        
        await message.reply(
            f"✅ **Уведомление отложено на {days} день(дней)!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"🕐 Новое время: {new_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число дней.", parse_mode='Markdown')


@dp.message_handler(state=SnoozeStates.waiting_for_months)
async def snooze_set_months(message: types.Message, state: FSMContext):
    try:
        months = int(message.text)
        if months <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число месяцев.", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notif_id = data.get('snooze_notif_id')
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        days = months * 30
        new_time = get_current_time() + timedelta(days=days)
        
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        if new_time.tzinfo is None:
            new_time = tz.localize(new_time)
        
        notifications[notif_id]['time'] = new_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        logger.info(f"Уведомление {notif_id} отложено на {months} месяцев")
        
        await message.reply(
            f"✅ **Уведомление отложено на {months} месяц(ев)!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"🕐 Новое время: {new_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число месяцев.", parse_mode='Markdown')


@dp.message_handler(state=SnoozeStates.waiting_for_specific_date)
async def snooze_set_specific_date(message: types.Message, state: FSMContext):
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
        
        data = await state.get_data()
        notif_id = data.get('snooze_notif_id')
        
        if not notif_id or notif_id not in notifications:
            await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
            await state.finish()
            return
        
        repeat_count = notifications[notif_id].get('repeat_count', 0)
        
        tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
        if notify_time.tzinfo is None:
            notify_time = tz.localize(notify_time)
        
        notifications[notif_id]['time'] = notify_time.isoformat()
        notifications[notif_id]['notified'] = False
        notifications[notif_id]['is_completed'] = False
        notifications[notif_id]['is_repeat'] = True
        notifications[notif_id]['repeat_count'] = repeat_count + 1
        notifications[notif_id]['repeat_type'] = 'no'
        notifications[notif_id]['last_repeat_time'] = None
        
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        logger.info(f"Уведомление {notif_id} отложено на конкретную дату")
        
        await message.reply(
            f"✅ **Уведомление отложено!**\n\n"
            f"📝 {notifications[notif_id]['text']}\n"
            f"🔄 Повторение #{repeat_count + 1}\n"
            f"🕐 Новое время: {notify_time.strftime('%d.%m.%Y в %H:%M')}\n\n"
            f"ℹ️ Уведомление будет повторяться каждый час, пока вы не отметите его как выполненное.",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(message)
        await state.finish()
    except Exception as e:
        logger.error(f"Ошибка откладывания уведомления: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "cancel_snooze", state='*')
async def cancel_snooze(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(callback.from_user.id, "✅ **Откладывание отменено**", parse_mode='Markdown')
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('complete_'))
async def handle_complete(callback: types.CallbackQuery):
    notif_id = callback.data.replace('complete_', '')
    logger.info(f"Уведомление {notif_id} отмечено как выполненное пользователем {callback.from_user.id}")
    
    if notif_id in notifications:
        notif_num = notifications[notif_id].get('num', notif_id)
        
        await sync_notification_to_calendar(notif_id, 'delete')
        
        del notifications[notif_id]
        
        new_notifications = {}
        for i, (nid, notif) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
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
        
        await show_backup_notification(callback.message)
    else:
        await callback.answer("Уведомление уже обработано")
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('complete_today_'))
async def handle_complete_today(callback: types.CallbackQuery):
    notif_id = callback.data.replace('complete_today_', '')
    logger.info(f"Уведомление {notif_id} отмечено как выполненное сегодня пользователем {callback.from_user.id}")
    
    if notif_id in notifications:
        notif = notifications[notif_id]
        notif_num = notif.get('num', notif_id)
        
        now = get_current_time()
        notif['last_trigger'] = now.isoformat()
        notif['notified'] = False
        notif['is_completed'] = False
        notif['is_repeat'] = False
        notif['repeat_count'] = 0
        notif['last_repeat_time'] = None
        save_data()
        
        await sync_notification_to_calendar(notif_id, 'update')
        
        try:
            await bot.delete_message(callback.from_user.id, callback.message.message_id)
        except:
            pass
        
        repeat_type = notif.get('repeat_type', 'weekdays')
        hour = notif.get('repeat_hour', 0)
        minute = notif.get('repeat_minute', 0)
        
        if repeat_type == 'weekdays':
            weekdays_list = notif.get('weekdays_list', [])
            next_time = get_next_weekday(weekdays_list, hour, minute, now + timedelta(seconds=1))
        elif repeat_type == 'every_day':
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            next_time = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
            if next_time <= now:
                next_time += timedelta(days=1)
        else:
            next_time = None
        
        if next_time:
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            local_next = next_time.astimezone(tz) if next_time.tzinfo else next_time
            notif['next_time'] = local_next.isoformat()
            save_data()
            next_time_str = local_next.strftime('%d.%m.%Y в %H:%M')
        else:
            next_time_str = "не определено"
        
        await bot.send_message(
            callback.from_user.id,
            f"✅ **Уведомление #{notif_num} отмечено как выполненное на сегодня!**\n\n"
            f"📝 {notif['text']}\n"
            f"⏰ Следующее срабатывание: {next_time_str}\n\n"
            f"🔄 Счетчик повторений сброшен",
            parse_mode='Markdown'
        )
        
        await show_backup_notification(callback.message)
    else:
        await callback.answer("Уведомление уже обработано")
    
    await callback.answer()


async def list_notifications_handler(message: types.Message, state: FSMContext):
    """Функция отображения списка уведомлений"""
    logger.info(f"Пользователь {message.from_user.id} запросил список уведомлений")
    
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    sorted_notifs = sorted(notifications.items(), key=lambda x: int(x[0]))
    
    for notif_id, notif in sorted_notifs:
        if notif.get('is_completed', False):
            continue
        
        repeat_type = notif.get('repeat_type', 'no')
        is_repeat = notif.get('is_repeat', False)
        repeat_count = notif.get('repeat_count', 0)
        
        repeat_text = ""
        next_time_str = ""
        status_emoji = "⏳"
        status_text = "ОЖИДАЕТ"
        
        if repeat_type == 'every_hour':
            repeat_text = f"\n🔄 **Повтор:** Каждый час"
            last_trigger = notif.get('last_trigger')
            if last_trigger:
                last_time = datetime.fromisoformat(last_trigger)
                if last_time.tzinfo is None:
                    last_time = tz.localize(last_time)
                next_time_str = f"\n⏰ **Последнее:** {last_time.strftime('%d.%m.%Y в %H:%M')}"
            status_emoji = "🔄"
            status_text = "АКТИВНО"
        elif repeat_type == 'every_day':
            hour = notif.get('repeat_hour', 0)
            minute = notif.get('repeat_minute', 0)
            repeat_text = f"\n🔄 **Повтор:** Каждый день в {hour:02d}:{minute:02d}"
            if notif.get('next_time'):
                next_time = datetime.fromisoformat(notif['next_time'])
                if next_time.tzinfo is None:
                    next_time = tz.localize(next_time)
                next_time_str = f"\n⏰ **Следующее:** {next_time.strftime('%d.%m.%Y в %H:%M')}"
            status_emoji = "🔄"
            status_text = "АКТИВНО"
        elif repeat_type == 'weekdays':
            hour = notif.get('repeat_hour', 0)
            minute = notif.get('repeat_minute', 0)
            days_names = [WEEKDAYS_NAMES[d] for d in notif.get('weekdays_list', [])]
            repeat_text = f"\n🔄 **Повтор:** По дням недели: {', '.join(days_names)} в {hour:02d}:{minute:02d}"
            if notif.get('next_time'):
                next_time = datetime.fromisoformat(notif['next_time'])
                if next_time.tzinfo is None:
                    next_time = tz.localize(next_time)
                next_time_str = f"\n⏰ **Следующее:** {next_time.strftime('%d.%m.%Y в %H:%M')}"
            status_emoji = "🔄"
            status_text = "АКТИВНО"
        elif repeat_type == 'no' and notif.get('time'):
            notify_time = datetime.fromisoformat(notif['time'])
            if notify_time.tzinfo is None:
                notify_time = tz.localize(notify_time)
            local_time = notify_time.astimezone(tz)
            now = get_current_time()
            
            if notif.get('notified', False):
                last_repeat_str = notif.get('last_repeat_time')
                if last_repeat_str:
                    last_repeat = datetime.fromisoformat(last_repeat_str)
                    if last_repeat.tzinfo is None:
                        last_repeat = tz.localize(last_repeat)
                    time_since_last = now - last_repeat
                    if time_since_last.total_seconds() >= 3600:
                        status_emoji = "⚠️"
                        status_text = "ПРОСРОЧЕНО"
                    else:
                        status_emoji = "🔄"
                        status_text = "ОЖИДАЕТ ПОВТОРА"
                else:
                    status_emoji = "⚠️"
                    status_text = "ПРОСРОЧЕНО"
            elif now >= local_time:
                status_emoji = "⏰"
                status_text = "СЕЙЧАС"
            else:
                status_emoji = "⏳"
                status_text = "ОЖИДАЕТ"
            
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
            
            repeat_info = ""
            if is_repeat:
                repeat_info = f"\n🔄 **Повторное напоминание #{repeat_count}**"
            
            text = (
                f"{status_emoji} **Уведомление #{notif.get('num', notif_id)}**{repeat_info}\n"
                f"📝 **Текст:** {notif['text']}\n"
                f"⏰ **Время:** {local_time.strftime('%d.%m.%Y в %H:%M')}\n"
                f"📊 **Статус:** {status_text}{time_left}"
            )
            
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
            )
            
            await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
            continue
        
        repeat_info = ""
        if is_repeat:
            repeat_info = f"\n🔄 **Повторное напоминание #{repeat_count}**"
        
        text = (
            f"{status_emoji} **Уведомление #{notif.get('num', notif_id)}**{repeat_info}\n"
            f"📝 **Текст:** {notif['text']}\n"
            f"📊 **Статус:** {status_text}{repeat_text}{next_time_str}"
        )
        
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{notif_id}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{notif_id}")
        )
        
        await message.reply(text, reply_markup=keyboard, parse_mode='Markdown')
    
    active_count = sum(1 for n in notifications.values() if not n.get('is_completed', False))
    await message.reply(
        f"📊 **Всего уведомлений:** {len(notifications)}\n"
        f"💡 **Активных:** {active_count}",
        parse_mode='Markdown'
    )


async def settings_menu_handler(message: types.Message, state: FSMContext):
    global notifications_enabled
    
    status_text = "🔕 Выкл" if not notifications_enabled else "🔔 Вкл"
    status_emoji = "🔕" if not notifications_enabled else "🔔"
    
    calendar_sync_status = "✅ Вкл" if get_calendar_sync_enabled() else "❌ Выкл"
    calendar_sync_emoji = "☁️" if get_calendar_sync_enabled() else "☁️"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(f"{status_emoji} Уведомления: {status_text}", callback_data="toggle_notifications"),
        InlineKeyboardButton(f"{calendar_sync_emoji} Синхр. с календарём: {calendar_sync_status}", callback_data="toggle_calendar_sync"),
        InlineKeyboardButton("📁 Выбрать папку на Яндекс.Диске", callback_data="select_backup_folder"),
        InlineKeyboardButton("🔢 Максимум бэкапов", callback_data="set_max_backups"),
        InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time"),
        InlineKeyboardButton("🌍 Часовой пояс", callback_data="set_timezone"),
        InlineKeyboardButton("🔑 Авторизация", callback_data="auth_yandex"),
        InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup_manual"),
        InlineKeyboardButton("📤 Восстановить из бэкапа", callback_data="restore_backup"),
        InlineKeyboardButton("🔄 Синхр. календарь → бот", callback_data="sync_calendar_to_bot"),
        InlineKeyboardButton("🔍 Проверить календарь", callback_data="check_calendar_connection"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="info")
    )
    
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=keyboard, parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "check_calendar_connection")
async def check_calendar_connection_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    token = get_user_token(user_id)
    
    if not token:
        await bot.send_message(
            callback.from_user.id,
            "❌ **Токен Яндекс не найден!**\n\n"
            "Сначала авторизуйтесь в настройках.",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    status_msg = await bot.send_message(
        callback.from_user.id,
        "🔍 **Проверка соединения с Яндекс.Календарём...**",
        parse_mode='Markdown'
    )
    
    calendar_api = YandexCalendarAPI(token)
    
    api_ok, api_message = await calendar_api.test_connection()
    
    if not api_ok:
        await status_msg.edit_text(
            f"❌ **Ошибка подключения к API**\n\n"
            f"📋 **Диагностика:**\n"
            f"{api_message}\n\n"
            f"💡 **Рекомендации:**\n"
            f"• Проверьте интернет-соединение\n"
            f"• Убедитесь, что в OAuth-приложении выбраны права calendar:read и calendar:write\n"
            f"• Попробуйте авторизоваться заново",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    calendar_ok, calendar_message, calendar_id = await calendar_api.test_calendar_access()
    
    if calendar_ok:
        await status_msg.edit_text(
            f"✅ **Соединение с Яндекс.Календарём установлено!**\n\n"
            f"📋 **Результаты проверки:**\n"
            f"• {api_message}\n"
            f"• {calendar_message}\n\n"
            f"🆔 **ID календаря:** `{calendar_id}`\n\n"
            f"🎉 Синхронизация с календарём работает корректно!",
            parse_mode='Markdown'
        )
    else:
        await status_msg.edit_text(
            f"⚠️ **API доступен, но есть проблемы с календарём**\n\n"
            f"📋 **Диагностика:**\n"
            f"• {api_message}\n"
            f"• {calendar_message}\n\n"
            f"💡 **Рекомендации:**\n"
            f"• Перейдите на calendar.yandex.ru и убедитесь, что у вас есть хотя бы один календарь\n"
            f"• Если календари есть, попробуйте задать им цвет в настройках\n"
            f"• Создайте новый календарь, если проблема не решается",
            parse_mode='Markdown'
        )
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "sync_calendar_to_bot")
async def sync_calendar_to_bot_handler(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    token = get_user_token(user_id)
    
    if not token:
        await bot.send_message(
            callback.from_user.id,
            "❌ **Нет доступа к Яндекс.Календарю!**\n\n"
            "Сначала авторизуйтесь в настройках.",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    status_msg = await bot.send_message(
        callback.from_user.id,
        "⏳ **Синхронизация календаря...**",
        parse_mode='Markdown'
    )
    
    calendar_api = YandexCalendarAPI(token)
    
    api_ok, api_message = await calendar_api.test_connection()
    if not api_ok:
        await status_msg.edit_text(
            f"❌ **Не удалось подключиться к календарю**\n\n"
            f"{api_message}",
            parse_mode='Markdown'
        )
        await callback.answer()
        return
    
    synced_count = await calendar_api.sync_calendar_to_bot()
    
    if synced_count > 0:
        await status_msg.edit_text(
            f"✅ **Синхронизация завершена!**\n\n"
            f"📥 Импортировано событий: {synced_count}\n"
            f"📝 Всего уведомлений: {len(notifications)}",
            parse_mode='Markdown'
        )
        await show_backup_notification(callback.message)
    else:
        await status_msg.edit_text(
            "ℹ️ **Новых событий для импорта не найдено**\n\n"
            "Убедитесь, что в календаре есть события на ближайшие 30 дней.",
            parse_mode='Markdown'
        )
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "toggle_calendar_sync")
async def toggle_calendar_sync(callback: types.CallbackQuery, state: FSMContext):
    current = config.get('calendar_sync_enabled', False)
    config['calendar_sync_enabled'] = not current
    save_data()
    
    status = "включена" if config['calendar_sync_enabled'] else "выключена"
    logger.info(f"Синхронизация с календарём {status} пользователем {callback.from_user.id}")
    
    if config['calendar_sync_enabled']:
        token = get_user_token(callback.from_user.id)
        if token:
            calendar_api = YandexCalendarAPI(token)
            api_ok, api_message = await calendar_api.test_connection()
            
            if not api_ok:
                await bot.send_message(
                    callback.from_user.id,
                    f"⚠️ **Синхронизация включена, но есть проблемы с подключением!**\n\n"
                    f"{api_message}\n\n"
                    f"Проверьте права доступа calendar:read и calendar:write\n"
                    f"и попробуйте проверить соединение позже через кнопку «🔍 Проверить календарь»",
                    parse_mode='Markdown'
                )
            else:
                for notif_id in notifications:
                    if notif_id not in calendar_sync:
                        await sync_notification_to_calendar(notif_id, 'create')
                
                await bot.send_message(
                    callback.from_user.id,
                    "✅ **Синхронизация с Яндекс Календарём включена!**\n\n"
                    "Все уведомления будут автоматически добавляться в календарь.\n\n"
                    "💡 Для синхронизации существующих событий используйте кнопку «Синхр. календарь → бот»",
                    parse_mode='Markdown'
                )
        else:
            await bot.send_message(
                callback.from_user.id,
                "✅ **Синхронизация включена, но требуется авторизация!**\n\n"
                "Авторизуйтесь в настройках для работы с календарём.",
                parse_mode='Markdown'
            )
    else:
        await bot.send_message(
            callback.from_user.id,
            "✅ **Синхронизация с Яндекс Календарём выключена!**",
            parse_mode='Markdown'
        )
    
    await settings_menu_handler(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data in ['edit_time_hours', 'edit_time_days', 'edit_time_months', 'edit_time_specific', 'edit_time_every_hour', 'edit_time_every_day', 'edit_time_weekdays'], state=NotificationStates.waiting_for_edit_time)
async def process_edit_time_type(callback: types.CallbackQuery, state: FSMContext):
    time_type = callback.data.replace("edit_time_", "")
    logger.info(f"Выбран тип времени для редактирования: {time_type}")
    
    data = await state.get_data()
    edit_id = data.get('edit_id')
    
    if not edit_id or edit_id not in notifications:
        await callback.answer("❌ Уведомление не найдено")
        await state.finish()
        return
    
    await state.update_data(edit_time_type=time_type, edit_id=edit_id)
    
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
            "🗓️ **Введите новую дату**\n\n"
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
    elif time_type == 'every_hour':
        notifications[edit_id]['repeat_type'] = 'every_hour'
        notifications[edit_id]['notified'] = False
        notifications[edit_id]['is_completed'] = False
        notifications[edit_id]['is_repeat'] = False
        notifications[edit_id]['repeat_count'] = 0
        notifications[edit_id]['last_trigger'] = get_current_time().isoformat()
        save_data()
        
        await sync_notification_to_calendar(edit_id, 'update')
        
        await bot.send_message(
            callback.from_user.id,
            f"✅ **Уведомление #{notifications[edit_id].get('num', edit_id)} изменено!**\n"
            f"📝 {notifications[edit_id]['text']}\n"
            f"📅 **Тип:** Каждый час\n"
            f"🕐 Будет напоминать каждый час\n",
            parse_mode='Markdown'
        )
        await show_backup_notification(callback.message)
        await state.finish()
    elif time_type == 'every_day':
        await send_with_auto_delete(
            callback.from_user.id,
            "⏰ **Введите новое время для ежедневного уведомления**\n\n"
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
            keyboard.add(InlineKeyboardButton(name, callback_data=f"edit_weekday_{day}"))
        keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="edit_weekdays_done"))
        keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
        
        await bot.send_message(
            callback.from_user.id,
            "📅 **Выберите новые дни недели**\n\n"
            "Нажимайте на дни, чтобы выбрать/отменить.\n"
            "Когда закончите, нажмите «✅ Готово»\n\n"
            "⏰ **У вас есть 3 минуты**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.update_data(edit_selected_weekdays=[])
        await NotificationStates.waiting_for_weekdays.set()
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('do_edit_text_'), state='*')
async def edit_notification_text_handler(callback: types.CallbackQuery, state: FSMContext):
    edit_id = callback.data.replace("do_edit_text_", "")
    logger.info(f"Пользователь {callback.from_user.id} выбрал изменение текста для уведомления {edit_id}")
    
    if edit_id not in notifications:
        logger.error(f"Уведомление {edit_id} не найдено")
        await callback.answer("❌ Уведомление не найдено")
        return
    
    await state.update_data(edit_id=edit_id)
    
    await bot.send_message(
        callback.from_user.id,
        "✏️ **Введите новый текст уведомления:**\n\n"
        f"📝 Старый текст: {notifications[edit_id]['text']}\n\n"
        "⏰ **У вас есть 3 минуты**\n\n"
        "💡 Для отмены отправьте /cancel",
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_edit_text.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('do_edit_time_'), state='*')
async def edit_notification_time_handler(callback: types.CallbackQuery, state: FSMContext):
    edit_id = callback.data.replace("do_edit_time_", "")
    logger.info(f"Пользователь {callback.from_user.id} выбрал изменение времени для уведомления {edit_id}")
    
    if edit_id not in notifications:
        logger.error(f"Уведомление {edit_id} не найдено")
        await callback.answer("❌ Уведомление не найдено")
        return
    
    await state.update_data(edit_id=edit_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="edit_time_hours"),
        InlineKeyboardButton("📅 В днях", callback_data="edit_time_days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="edit_time_months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="edit_time_specific"),
        InlineKeyboardButton("🕐 Каждый час", callback_data="edit_time_every_hour"),
        InlineKeyboardButton("📅 Каждый день", callback_data="edit_time_every_day"),
        InlineKeyboardButton("📆 По дням недели", callback_data="edit_time_weekdays"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")
    )
    
    await bot.send_message(
        callback.from_user.id,
        "⏱️ **Выберите новый период для уведомления:**\n\n"
        "⏰ **У вас есть 3 минуты**",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await NotificationStates.waiting_for_edit_time.set()
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_'), state='*')
async def edit_notification_menu(callback: types.CallbackQuery, state: FSMContext):
    notif_id = callback.data.replace('edit_', '')
    logger.info(f"Пользователь {callback.from_user.id} открыл меню редактирования для уведомления {notif_id}")
    
    if notif_id not in notifications:
        logger.warning(f"Уведомление {notif_id} не найдено")
        await callback.answer("Уведомление не найдено")
        return
    
    await state.update_data(edit_id=notif_id)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить текст", callback_data=f"do_edit_text_{notif_id}"),
        InlineKeyboardButton("⏰ Изменить время", callback_data=f"do_edit_time_{notif_id}")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
    
    await bot.send_message(
        callback.from_user.id,
        f"✏️ **Что хотите изменить в уведомлении #{notifications[notif_id].get('num', notif_id)}?**\n\n"
        f"📝 Текст: {notifications[notif_id]['text'][:50]}...",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("edit_weekday_"), state=NotificationStates.waiting_for_weekdays)
async def edit_select_weekday(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.replace("edit_weekday_", ""))
    data = await state.get_data()
    selected = data.get('edit_selected_weekdays', [])
    
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    
    await state.update_data(edit_selected_weekdays=selected)
    
    keyboard = InlineKeyboardMarkup(row_width=3)
    for name, d in WEEKDAYS_BUTTONS:
        text = f"✅ {name}" if d in selected else name
        keyboard.add(InlineKeyboardButton(text, callback_data=f"edit_weekday_{d}"))
    keyboard.add(InlineKeyboardButton("✅ Готово", callback_data="edit_weekdays_done"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
    
    selected_names = [WEEKDAYS_NAMES[d] for d in sorted(selected)]
    status_text = f"Выбрано: {', '.join(selected_names) if selected else 'ничего не выбрано'}"
    
    await bot.edit_message_text(
        f"📅 **Выберите новые дни недели**\n\n{status_text}\n\n"
        "Нажимайте на дни, чтобы выбрать/отменить.\n"
        "Когда закончите, нажмите «✅ Готово»\n\n"
        "⏰ **У вас есть 3 минуты**",
        callback.from_user.id,
        callback.message.message_id,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "edit_weekdays_done", state=NotificationStates.waiting_for_weekdays)
async def edit_weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('edit_selected_weekdays', [])
    
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день недели!")
        return
    
    await state.update_data(weekdays_list=selected)
    
    await send_with_auto_delete(
        callback.from_user.id,
        "⏰ **Введите новое время для уведомления**\n\n"
        "📝 Формат: `ЧЧ:ММ`\n\n"
        "📝 Примеры: `09:00` или `18:30`\n\n"
        "⏰ **У вас есть 3 минуты**\n\n"
        "💡 Для отмены отправьте /cancel",
        delay=180
    )
    await NotificationStates.waiting_for_weekday_time.set()
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_edit_text)
async def save_edited_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    edit_id = data.get('edit_id')
    
    logger.info(f"Сохранение нового текста для уведомления {edit_id}")
    
    if not edit_id or edit_id not in notifications:
        logger.error(f"Уведомление {edit_id} не найдено при сохранении текста")
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')
        await state.finish()
        return
    
    old_text = notifications[edit_id]['text']
    notifications[edit_id]['text'] = message.text
    save_data()
    
    await sync_notification_to_calendar(edit_id, 'update')
    
    logger.info(f"Текст уведомления {edit_id} изменен: '{old_text[:30]}...' -> '{message.text[:30]}...'")
    await message.reply(f"✅ **Текст уведомления изменен!**\n\nСтарый текст: {old_text}\n\nНовый текст: {message.text}", parse_mode='Markdown')
    
    await show_backup_notification(message)
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "cancel_edit", state='*')
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    logger.info(f"Пользователь {callback.from_user.id} отменил редактирование")
    await state.finish()
    await bot.send_message(callback.from_user.id, "✅ **Редактирование отменено**", parse_mode='Markdown')
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def handle_delete_notification(callback: types.CallbackQuery):
    notif_id = callback.data.replace('delete_', '')
    logger.info(f"Пользователь {callback.from_user.id} удалил уведомление {notif_id}")
    
    if notif_id in notifications:
        notif_num = notifications[notif_id].get('num', notif_id)
        
        await sync_notification_to_calendar(notif_id, 'delete')
        
        del notifications[notif_id]
        
        new_notifications = {}
        for i, (nid, notif) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
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
        
        await show_backup_notification(callback.message)
    else:
        await callback.answer("Уведомление уже удалено")
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery, state: FSMContext):
    global notifications_enabled
    notifications_enabled = not notifications_enabled
    config['notifications_enabled'] = notifications_enabled
    save_data()
    
    status = "включены" if notifications_enabled else "выключены"
    logger.info(f"Уведомления {status} пользователем {callback.from_user.id}")
    await bot.send_message(
        callback.from_user.id,
        f"✅ **Уведомления {status}!**",
        parse_mode='Markdown'
    )
    await settings_menu_handler(callback.message, state)
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
            await show_backup_notification(message)
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
        await show_backup_notification(message)
    except ValueError:
        await message.reply("❌ **Ошибка!** Формат `ЧЧ:ММ`", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    access = False
    access_message = "Не авторизован"
    
    if ADMIN_ID:
        access, access_message = await check_yandex_access(ADMIN_ID)
    
    calendar_sync_status = "✅ Вкл" if get_calendar_sync_enabled() else "❌ Выкл"
    
    info = f"""
📊 **СТАТИСТИКА**

🤖 **Версия:** v{BOT_VERSION} ({BOT_VERSION_DATE} {BOT_VERSION_TIME})

📝 **Уведомлений:** `{len(notifications)}`
💾 **Максимум бэкапов:** `{config.get('max_backups', 5)}`
📁 **Путь бэкапов:** `{config['backup_path']}`
🕐 **Проверка:** `{config.get('daily_check_time', '06:00')}`
🌍 **Часовой пояс:** `{config.get('timezone', 'Europe/Moscow')}`
🕐 **Текущее время:** `{get_current_time().strftime('%d.%m.%Y %H:%M:%S')}`
🔔 **Уведомления:** `{'Вкл' if notifications_enabled else 'Выкл'}`
📅 **Синхр. с календарём:** `{calendar_sync_status}`

🔑 **Яндекс.Диск:** `{'✅ Доступен' if access else '❌ ' + access_message}`
"""
    await bot.send_message(callback.from_user.id, info, parse_mode='Markdown')
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "create_backup_manual")
async def manual_backup_settings(callback: types.CallbackQuery, state: FSMContext):
    if not ADMIN_ID:
        await bot.send_message(callback.from_user.id, "❌ Ошибка: ADMIN_ID не задан", parse_mode='Markdown')
        return
    
    status_msg = await bot.send_message(callback.from_user.id, "⏳ **Создание бэкапа...**", parse_mode='Markdown')
    success, _, location = await create_backup(ADMIN_ID)
    
    if success:
        await status_msg.edit_text(f"✅ **Бэкап создан** ({location})", parse_mode='Markdown')
        await asyncio.sleep(5)
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
    
    for folder in folders[:10]:
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
    
    await settings_menu_handler(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_folder_selection")
async def cancel_folder_selection(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu_handler(callback.message, state)
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
    await settings_menu_handler(callback.message, state)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "cancel_tz")
async def cancel_tz(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu_handler(callback.message, state)
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
    for backup in backups[:10]:
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
            f"📝 Уведомлений: {len(notifications)}\n\n"
            f"⚠️ **Важно:** Флаги уведомлений сброшены. Просроченные уведомления не будут отправлены сразу, они будут ждать следующего срабатывания или повторных напоминаний.",
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
    await settings_menu_handler(callback.message, state)
    await callback.answer()


@dp.message_handler(content_types=['document'], state=SettingsStates.waiting_for_upload_backup)
async def receive_backup_file(message: types.Message, state: FSMContext):
    global notifications, config, calendar_sync
    
    try:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        downloaded_file = await bot.download_file(file.file_path)
        backup_data = json.loads(downloaded_file.read().decode('utf-8'))
        
        if 'notifications' in backup_data:
            notifications = backup_data['notifications']
            
            # ВАЖНОЕ ИСПРАВЛЕНИЕ: Сбрасываем флаги уведомлений при восстановлении
            now = get_current_time()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            
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
                
                # СБРАСЫВАЕМ ФЛАГИ УВЕДОМЛЕНИЙ
                notif['notified'] = False
                
                repeat_type = notif.get('repeat_type', 'no')
                if repeat_type in ['every_day', 'weekdays']:
                    if notif.get('next_time'):
                        next_time = datetime.fromisoformat(notif['next_time'])
                        if next_time.tzinfo is None:
                            next_time = tz.localize(next_time)
                        if repeat_type == 'every_day':
                            notif['last_trigger'] = (next_time - timedelta(days=1)).isoformat()
                        elif repeat_type == 'weekdays':
                            notif['last_trigger'] = (next_time - timedelta(days=7)).isoformat()
                    else:
                        notif['last_trigger'] = None
                elif repeat_type == 'every_hour':
                    notif['last_trigger'] = now.isoformat()
                
                if repeat_type == 'no' and notif.get('time'):
                    notify_time = datetime.fromisoformat(notif['time'])
                    if notify_time.tzinfo is None:
                        notify_time = tz.localize(notify_time)
                    
                    if notify_time < now:
                        notif['notified'] = False
                        notif['last_repeat_time'] = None
            
            if 'config' in backup_data:
                config = backup_data['config']
            if 'calendar_sync' in backup_data:
                calendar_sync = backup_data['calendar_sync']
            save_data()
            save_calendar_sync()
            
            await message.reply(
                f"✅ **Данные восстановлены!**\n\n"
                f"📝 Уведомлений: {len(notifications)}\n\n"
                f"⚠️ **Важно:** Флаги уведомлений сброшены. Просроченные уведомления не будут отправлены сразу.",
                parse_mode='Markdown'
            )
            await show_backup_notification(message)
        else:
            await message.reply("❌ **Неверный формат бэкапа!**", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка восстановления из бэкапа: {e}")
        await message.reply(f"❌ **Ошибка:** {str(e)}", parse_mode='Markdown')
    
    await state.finish()


@dp.message_handler(commands=['restart'])
async def restart_bot(message: types.Message):
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        logger.info(f"Перезапуск бота пользователем {message.from_user.id}")
        await message.reply("🔄 **Перезапуск...**", parse_mode='Markdown')
        await asyncio.sleep(2)
        os._exit(0)


@dp.message_handler(commands=['version'])
async def show_version(message: types.Message):
    await message.reply(
        f"🤖 **Бот для уведомлений**\n"
        f"📌 **Версия:** v{BOT_VERSION}\n"
        f"📅 **Дата:** {BOT_VERSION_DATE}\n"
        f"🕐 **Время сборки:** {BOT_VERSION_TIME}",
        parse_mode='Markdown'
    )


@dp.message_handler(commands=['cancel'], state='*')
async def cancel_operation(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.reply("❌ **Нет активных операций для отмены**", parse_mode='Markdown')
        return
    
    logger.info(f"Отмена операции {current_state} пользователем {message.from_user.id}")
    await state.finish()
    await message.reply("✅ **Операция отменена!**", parse_mode='Markdown')
    await cmd_start(message, state)


async def on_startup(dp):
    init_folders()
    load_data()
    
    new_notifications = {}
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