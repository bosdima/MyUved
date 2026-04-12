import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlencode
from io import BytesIO
import pytz
import re
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

# ==================== НАСТРОЙКА ====================
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

if not all([BOT_TOKEN, CLIENT_ID, CLIENT_SECRET]):
    logger = logging.getLogger(__name__)
    logger.error("❌ Ошибка: Не все переменные окружения заданы!")
    exit(1)

BOT_VERSION = "4.05"
BOT_VERSION_DATE = "12.04.2026"
BOT_VERSION_TIME = "11:00"

# Логирование
log_file = 'bot_debug.log'
file_handler = RotatingFileHandler(log_file, maxBytes=100*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# ==================== КОНСТАНТЫ ====================
DATA_FILE = 'notifications.json'
CONFIG_FILE = 'config.json'
TOKEN_FILE = 'user_tokens.json'
BACKUP_DIR = 'backups'
CALENDAR_SYNC_FILE = 'calendar_sync.json'

YANDEX_API_BASE = "https://cloud-api.yandex.net/v1/disk"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"
YANDEX_CALENDAR_API = "https://api.calendar.yandex.net/calendar/v1"

TIMEZONES = {
    'Москва (UTC+3)': 'Europe/Moscow', 'Санкт-Петербург (UTC+3)': 'Europe/Moscow',
    'Калининград (UTC+2)': 'Europe/Kaliningrad', 'Екатеринбург (UTC+5)': 'Asia/Yekaterinburg',
    'Новосибирск (UTC+7)': 'Asia/Novosibirsk', 'Красноярск (UTC+7)': 'Asia/Krasnoyarsk',
    'Иркутск (UTC+8)': 'Asia/Irkutsk', 'Владивосток (UTC+10)': 'Asia/Vladivostok',
    'Магадан (UTC+11)': 'Asia/Magadan', 'Камчатка (UTC+12)': 'Asia/Kamchatka'
}

WEEKDAYS_BUTTONS = [("Пн",0), ("Вт",1), ("Ср",2), ("Чт",3), ("Пт",4), ("Сб",5), ("Вс",6)]
WEEKDAYS_NAMES = {0:"Понедельник",1:"Вторник",2:"Среда",3:"Четверг",4:"Пятница",5:"Суббота",6:"Воскресенье"}

# ==================== ГЛОБАЛЬНЫЕ ДАННЫЕ ====================
notifications: Dict = {}
config: Dict = {}
user_tokens: Dict[int, str] = {}
calendar_sync: Dict = {}
notifications_enabled = True

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_current_time():
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    return datetime.now(tz)

def parse_date(date_str: str) -> Optional[datetime]:
    date_str = date_str.strip()
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    patterns = [
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s+(\d{1,2}):(\d{2})$', lambda d,m,y,h,mi: tz.localize(datetime(int(y) if int(y)>100 else 2000+int(y), int(m), int(d), int(h), int(mi)))),
        (r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', lambda d,m,y: tz.localize(datetime(int(y) if int(y)>100 else 2000+int(y), int(m), int(d), now.hour, now.minute))),
        (r'^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})$', lambda d,m,h,mi: tz.localize(datetime(now.year, int(m), int(d), int(h), int(mi))) if tz.localize(datetime(now.year, int(m), int(d), int(h), int(mi))) > now else tz.localize(datetime(now.year+1, int(m), int(d), int(h), int(mi)))),
        (r'^(\d{1,2})\.(\d{1,2})$', lambda d,m: tz.localize(datetime(now.year, int(m), int(d), now.hour, now.minute)) if tz.localize(datetime(now.year, int(m), int(d), now.hour, now.minute)) > now else tz.localize(datetime(now.year+1, int(m), int(d), now.hour, now.minute)))
    ]
    
    for pattern, builder in patterns:
        match = re.match(pattern, date_str)
        if match:
            try:
                return builder(*match.groups())
            except:
                return None
    return None

def get_next_weekday(target_weekdays: List[int], hour: int, minute: int, from_date: datetime = None) -> Optional[datetime]:
    now = from_date or get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    if now.tzinfo is None:
        now = tz.localize(now)
    
    if now.weekday() in target_weekdays:
        today = tz.localize(datetime(now.year, now.month, now.day, hour, minute))
        if today > now:
            return today
    
    for i in range(1, 15):
        next_date = now + timedelta(days=i)
        if next_date.weekday() in target_weekdays:
            return tz.localize(datetime(next_date.year, next_date.month, next_date.day, hour, minute))
    return None

def get_auth_url() -> str:
    return f"{YANDEX_OAUTH_URL}?{urlencode({'response_type':'code','client_id':CLIENT_ID,'redirect_uri':REDIRECT_URI})}"

async def get_access_token(auth_code: str) -> Optional[str]:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post("https://oauth.yandex.ru/token", data={"grant_type":"authorization_code","code":auth_code,"client_id":CLIENT_ID,"client_secret":CLIENT_SECRET}) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("access_token")
        except Exception as e:
            logger.error(f"Ошибка получения токена: {e}")
    return None

# ==================== КЛАССЫ API ====================
class YandexCalendarAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"OAuth {token}", "Content-Type": "application/json"}
    
    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{YANDEX_CALENDAR_API}/calendars", headers=self.headers, timeout=15) as resp:
                    if resp.status == 200:
                        return True, "Токен действителен, API доступен"
                    elif resp.status == 401:
                        return False, "Токен недействителен (истек или отозван)"
                    return False, f"Ошибка API ({resp.status})"
        except asyncio.TimeoutError:
            return False, "Таймаут подключения"
        except Exception as e:
            return False, f"Ошибка: {str(e)[:100]}"
    
    async def get_calendars(self) -> Optional[List[Dict]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{YANDEX_CALENDAR_API}/calendars", headers=self.headers, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('items') or data.get('calendars') or []
        except Exception as e:
            logger.error(f"Ошибка получения календарей: {e}")
        return None
    
    async def get_default_calendar_id(self) -> str:
        calendars = await self.get_calendars()
        if not calendars:
            return 'primary'
        for cal in calendars:
            if cal.get('primary'):
                return cal.get('id', 'primary')
        return calendars[0].get('id', 'primary')
    
    async def create_event(self, summary: str, start_time: datetime, description: str = "", calendar_id: str = None) -> Optional[str]:
        try:
            calendar_id = calendar_id or await self.get_default_calendar_id()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            start = start_time if start_time.tzinfo else tz.localize(start_time)
            end = start + timedelta(hours=1)
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{YANDEX_CALENDAR_API}/calendars/{calendar_id}/events", headers=self.headers, timeout=15, json={
                    "summary": summary[:255], "description": description[:1000],
                    "start": {"dateTime": start.isoformat(), "timeZone": config.get('timezone', 'Europe/Moscow')},
                    "end": {"dateTime": end.isoformat(), "timeZone": config.get('timezone', 'Europe/Moscow')},
                    "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]}
                }) as resp:
                    if resp.status in [200,201]:
                        return (await resp.json()).get('id')
        except Exception as e:
            logger.error(f"Ошибка создания события: {e}")
        return None
    
    async def delete_event(self, event_id: str, calendar_id: str = None) -> bool:
        try:
            calendar_id = calendar_id or await self.get_default_calendar_id()
            async with aiohttp.ClientSession() as session:
                async with session.delete(f"{YANDEX_CALENDAR_API}/calendars/{calendar_id}/events/{event_id}", headers=self.headers, timeout=15) as resp:
                    return resp.status in [200,204]
        except Exception as e:
            logger.error(f"Ошибка удаления события: {e}")
        return False
    
    async def sync_calendar_to_bot(self) -> int:
        synced = 0
        try:
            now = get_current_time()
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{YANDEX_CALENDAR_API}/calendars/{await self.get_default_calendar_id()}/events", headers=self.headers, params={
                    'timeMin': now.isoformat(), 'timeMax': (now + timedelta(days=30)).isoformat(), 'singleEvents': 'true'
                }, timeout=15) as resp:
                    if resp.status == 200:
                        for event in (await resp.json()).get('items', []):
                            event_id = event.get('id')
                            if any(s.get('calendar_event_id') == event_id for s in calendar_sync.values()):
                                continue
                            start = event.get('start', {}).get('dateTime')
                            if start:
                                notif_id = str(len(notifications) + 1)
                                notifications[notif_id] = {
                                    'text': event.get('summary', 'Без названия'), 'time': start, 'created': now.isoformat(),
                                    'notified': False, 'is_completed': False, 'num': int(notif_id), 'repeat_type': 'no',
                                    'is_repeat': False, 'repeat_count': 0, 'from_calendar': True
                                }
                                calendar_sync[notif_id] = {'calendar_event_id': event_id, 'last_sync': now.isoformat()}
                                synced += 1
                        save_calendar_sync()
                        save_data()
        except Exception as e:
            logger.error(f"Ошибка синхронизации: {e}")
        return synced

class YandexDiskAPI:
    def __init__(self, token): self.token = token; self.headers = {"Authorization": f"OAuth {token}", "Content-Type": "application/json"}
    
    async def check_access_async(self) -> Tuple[bool, str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{YANDEX_API_BASE}/", headers=self.headers) as resp:
                    if resp.status != 200:
                        return False, f"Нет доступа (код: {resp.status})"
                test_folder = f"/test_{int(datetime.now().timestamp())}"
                async with session.put(f"{YANDEX_API_BASE}/resources", headers=self.headers, params={"path": test_folder}) as put:
                    if put.status in [200,201,202]:
                        await session.delete(f"{YANDEX_API_BASE}/resources", headers=self.headers, params={"path": test_folder})
                        return True, "Есть права на запись"
                return False, "Нет прав на запись"
        except Exception as e:
            return False, str(e)
    
    def create_folder(self, folder_path):
        try:
            r = requests.put(f"{YANDEX_API_BASE}/resources", headers=self.headers, params={"path": folder_path}, timeout=10)
            return r.status_code in [200,201,202]
        except: return False
    
    def upload_file(self, local_path, remote_path):
        try:
            r = requests.get(f"{YANDEX_API_BASE}/resources/upload", headers=self.headers, params={"path": remote_path, "overwrite": "true"}, timeout=10)
            if r.status_code == 200:
                with open(local_path, 'rb') as f:
                    return requests.put(r.json().get("href"), files={"file": f}, timeout=30).status_code == 201
        except Exception as e: logger.error(f"Ошибка загрузки: {e}")
        return False
    
    def list_files(self, folder_path):
        try:
            r = requests.get(f"{YANDEX_API_BASE}/resources", headers=self.headers, params={"path": folder_path, "limit": 100}, timeout=10)
            if r.status_code == 200:
                return [i for i in r.json().get("_embedded", {}).get("items", []) if i.get("type") == "file"]
        except: pass
        return []
    
    def delete_file(self, remote_path):
        try:
            r = requests.delete(f"{YANDEX_API_BASE}/resources", headers=self.headers, params={"path": remote_path, "permanently": "true"}, timeout=10)
            return r.status_code in [200,202,204]
        except: return False
    
    def download_file(self, remote_path, local_path):
        try:
            r = requests.get(f"{YANDEX_API_BASE}/resources/download", headers=self.headers, params={"path": remote_path}, timeout=10)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    f.write(requests.get(r.json().get("href"), timeout=30).content)
                return True
        except: return False

# ==================== СОСТОЯНИЯ ====================
class AuthStates(StatesGroup): waiting_for_auth_method = State(); waiting_for_yandex_code = State(); waiting_for_direct_token = State()
class NotificationStates(StatesGroup): waiting_for_text = State(); waiting_for_time_type = State(); waiting_for_hours = State(); waiting_for_days = State(); waiting_for_months = State(); waiting_for_specific_date = State(); waiting_for_weekdays = State(); waiting_for_weekday_time = State(); waiting_for_every_day_time = State(); waiting_for_edit_text = State(); waiting_for_edit_time = State()
class SettingsStates(StatesGroup): waiting_for_max_backups = State(); waiting_for_check_time = State(); waiting_for_upload_backup = State(); waiting_for_timezone = State(); waiting_for_new_folder_name = State()
class SnoozeStates(StatesGroup): waiting_for_snooze_type = State(); waiting_for_hours = State(); waiting_for_days = State(); waiting_for_months = State(); waiting_for_specific_date = State(); waiting_for_weekdays = State(); waiting_for_weekday_time = State(); waiting_for_every_day_time = State()

# ==================== РАБОТА С ДАННЫМИ ====================
def load_calendar_sync():
    global calendar_sync
    try:
        with open(CALENDAR_SYNC_FILE, 'r', encoding='utf-8') as f:
            calendar_sync = json.load(f)
    except: calendar_sync = {}

def save_calendar_sync():
    with open(CALENDAR_SYNC_FILE, 'w', encoding='utf-8') as f:
        json.dump(calendar_sync, f, indent=2, ensure_ascii=False)

def get_calendar_sync_enabled() -> bool:
    return config.get('calendar_sync_enabled', False)

async def sync_notification_to_calendar(notif_id: str, action: str = 'create'):
    if not get_calendar_sync_enabled() or not ADMIN_ID:
        return
    token = get_user_token(ADMIN_ID)
    if not token or notif_id not in notifications:
        return
    
    notif = notifications[notif_id]
    cal = YandexCalendarAPI(token)
    
    try:
        if action == 'create':
            event_time = datetime.fromisoformat(notif['time'])
            if event_time.tzinfo is None:
                event_time = pytz.timezone(config.get('timezone', 'Europe/Moscow')).localize(event_time)
            event_id = await cal.create_event(notif['text'][:100], event_time, f"Уведомление из бота\nТекст: {notif['text']}")
            if event_id:
                calendar_sync[notif_id] = {'calendar_event_id': event_id, 'last_sync': get_current_time().isoformat()}
                save_calendar_sync()
        elif action == 'delete':
            sync_data = calendar_sync.get(notif_id)
            if sync_data and await cal.delete_event(sync_data['calendar_event_id']):
                del calendar_sync[notif_id]
                save_calendar_sync()
    except Exception as e:
        logger.error(f"Ошибка синхронизации: {e}")

def init_folders():
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    for f in [DATA_FILE, CONFIG_FILE, CALENDAR_SYNC_FILE]:
        if not os.path.exists(f):
            with open(f, 'w', encoding='utf-8') as fp:
                json.dump({} if f != CONFIG_FILE else {'backup_path':'/MyUved_backups','max_backups':5,'daily_check_time':'06:00','notifications_enabled':True,'timezone':'Europe/Moscow','calendar_sync_enabled':False}, fp)

def load_data():
    global notifications, config, user_tokens, notifications_enabled, calendar_sync
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        notifications = json.load(f)
        for n in notifications.values():
            n.setdefault('is_completed', False)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
        notifications_enabled = config.get('notifications_enabled', True)
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                user_tokens = {int(k): v for k, v in json.load(f).items()}
        except: user_tokens = {}
    load_calendar_sync()

def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(notifications, f, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def save_user_token(user_id: int, token: str):
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

async def check_yandex_access_with_test(user_id: int) -> Tuple[bool, str, Optional[YandexDiskAPI]]:
    token = get_user_token(user_id)
    if not token:
        return False, "❌ Нет токена авторизации", None
    disk = YandexDiskAPI(token)
    ok, msg = await disk.check_access_async()
    if ok:
        disk.create_folder(config['backup_path'])
        return True, f"✅ {msg}", disk
    return False, f"❌ {msg}", None

async def check_yandex_access(user_id: int) -> Tuple[bool, str]:
    ok, msg, _ = await check_yandex_access_with_test(user_id)
    return ok, msg

async def get_yadisk_backups(user_id: int) -> List[Dict]:
    token = get_user_token(user_id)
    if not token:
        return []
    files = YandexDiskAPI(token).list_files(config['backup_path'])
    return sorted([f for f in files if f['name'].startswith('backup_') and f['name'].endswith('.json')], key=lambda x: x['name'], reverse=True)

async def restore_from_yadisk_backup(backup_name: str, user_id: int) -> bool:
    global notifications, config, calendar_sync
    try:
        disk = YandexDiskAPI(get_user_token(user_id))
        local_path = Path(BACKUP_DIR) / f"restore_{backup_name}"
        if disk.download_file(f"{config['backup_path']}/{backup_name}", str(local_path)):
            with open(local_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'notifications' in data:
                notifications = data['notifications']
                for n in notifications.values():
                    n.setdefault('is_completed', False)
                if 'config' in data:
                    config = data['config']
                if 'calendar_sync' in data:
                    calendar_sync = data['calendar_sync']
                save_data()
                save_calendar_sync()
                return True
    except Exception as e:
        logger.error(f"Ошибка восстановления: {e}")
    return False

async def create_backup(user_id: int = None) -> Tuple[bool, Optional[Path], Optional[str]]:
    try:
        timestamp = get_current_time().strftime('%Y%m%d_%H%M%S')
        backup_file = Path(BACKUP_DIR) / f'backup_{timestamp}.json'
        backup_data = {'notifications': notifications, 'config': config, 'calendar_sync': calendar_sync, 'timestamp': timestamp, 'version': BOT_VERSION}
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        if user_id and get_user_token(user_id):
            disk = YandexDiskAPI(get_user_token(user_id))
            ok, _ = await check_yandex_access(user_id)
            if ok:
                disk.create_folder(config['backup_path'])
                if disk.upload_file(str(backup_file), f"{config['backup_path']}/backup_{timestamp}.json"):
                    return True, backup_file, "Яндекс.Диск"
        return True, backup_file, "Telegram"
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

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("➕ Добавить уведомление"), KeyboardButton("📋 Список уведомлений"))
    kb.add(KeyboardButton("⚙️ Настройки"))
    return kb

def get_time_type_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    for name, cb in [("⏰ В часах", "hours"), ("📅 В днях", "days"), ("📆 В месяцах", "months"), ("🗓️ Конкретная дата", "specific"),
                     ("🕐 Каждый час", "every_hour"), ("📅 Каждый день", "every_day"), ("📆 По дням недели", "weekdays")]:
        kb.add(InlineKeyboardButton(name, callback_data=f"time_{cb}"))
    kb.add(InlineKeyboardButton("🔙 Назад к тексту", callback_data="back_to_text"))
    return kb

def get_weekday_keyboard(selected: List[int] = None):
    selected = selected or []
    kb = InlineKeyboardMarkup(row_width=3)
    for name, day in WEEKDAYS_BUTTONS:
        kb.add(InlineKeyboardButton(f"✅ {name}" if day in selected else name, callback_data=f"weekday_{day}"))
    kb.add(InlineKeyboardButton("✅ Готово", callback_data="weekdays_done"))
    return kb

def get_snooze_keyboard(notif_id: str):
    kb = InlineKeyboardMarkup(row_width=2)
    for v, u in [(1,"hours"),(3,"hours"),(6,"hours"),(12,"hours"),(1,"days"),(2,"days"),(3,"days"),(7,"days")]:
        kb.add(InlineKeyboardButton(f"⏰ На {v} {u}", callback_data=f"snooze_select_{notif_id}_{v}_{u}"))
    kb.add(InlineKeyboardButton("🎯 Свой вариант", callback_data=f"snooze_custom_{notif_id}"), InlineKeyboardButton("❌ Отмена", callback_data="cancel_snooze"))
    return kb

# ==================== УВЕДОМЛЕНИЯ ====================
async def check_notifications():
    global notifications_enabled
    while True:
        if notifications_enabled:
            now = get_current_time()
            tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
            
            for notif_id, notif in list(notifications.items()):
                if notif.get('is_completed', False):
                    continue
                
                rt = notif.get('repeat_type', 'no')
                
                # Ежечасные
                if rt == 'every_hour':
                    last = datetime.fromisoformat(notif.get('last_trigger', notif.get('created', now.isoformat())))
                    if last.tzinfo is None:
                        last = tz.localize(last)
                    if (now - last).total_seconds() >= 3600:
                        await send_notification(notif_id, notif, now, f"🕐 Напоминает каждый час")
                        notif['last_trigger'] = now.isoformat()
                        save_data()
                
                # Ежедневные
                elif rt == 'every_day':
                    hour, minute = notif.get('repeat_hour', 0), notif.get('repeat_minute', 0)
                    today_trigger = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    last = notif.get('last_trigger')
                    if (not last or datetime.fromisoformat(last).date() < now.date()) and now >= today_trigger:
                        await send_notification(notif_id, notif, now)
                        notif['last_trigger'] = now.isoformat()
                        notif['is_repeat'] = False
                        save_data()
                
                # По дням недели
                elif rt == 'weekdays':
                    hour, minute = notif.get('repeat_hour', 0), notif.get('repeat_minute', 0)
                    last = notif.get('last_trigger')
                    next_time = get_next_weekday(notif.get('weekdays_list', []), hour, minute, now)
                    if next_time and next_time <= now and (not last or datetime.fromisoformat(last) < next_time):
                        await send_notification(notif_id, notif, now)
                        notif['last_trigger'] = now.isoformat()
                        notif['is_repeat'] = False
                        save_data()
                
                # Одноразовые
                elif rt == 'no' and notif.get('time'):
                    notify_time = datetime.fromisoformat(notif['time'])
                    if notify_time.tzinfo is None:
                        notify_time = tz.localize(notify_time)
                    
                    if not notif.get('notified', False) and now >= notify_time:
                        await send_notification(notif_id, notif, notify_time)
                        notif['notified'] = True
                        notif['last_repeat_time'] = now.isoformat()
                        save_data()
                    elif notif.get('notified', False):
                        last = notif.get('last_repeat_time')
                        if last:
                            last_time = datetime.fromisoformat(last)
                            if last_time.tzinfo is None:
                                last_time = tz.localize(last_time)
                            if (now - last_time).total_seconds() >= 3600:
                                rc = notif.get('repeat_count', 0) + 1
                                await send_notification(notif_id, notif, notify_time, f"🕐 Пропущено с {notify_time.strftime('%d.%m.%Y %H:%M')}", rc)
                                notif['last_repeat_time'] = now.isoformat()
                                notif['repeat_count'] = rc
                                save_data()
        await asyncio.sleep(30)

async def send_notification(notif_id: str, notif: dict, time: datetime, extra: str = "", repeat_count: int = 0):
    is_repeat = notif.get('is_repeat', False)
    rc = repeat_count or notif.get('repeat_count', 0)
    prefix = "ПОВТОРНОЕ НАПОМИНАНИЕ" if is_repeat or repeat_count else "НАПОМИНАНИЕ"
    num = f" #{rc}" if rc else ""
    text = f"🔔 **{prefix}{num}**\n\n📝 {notif['text']}\n\n⏰ {time.strftime('%d.%m.%Y %H:%M:%S')}"
    if extra:
        text += f"\n\n{extra}"
    
    kb = InlineKeyboardMarkup(row_width=2)
    cb_suffix = "_today" if notif.get('repeat_type') in ['every_day', 'weekdays'] else ""
    kb.add(InlineKeyboardButton("✅ Выполнено", callback_data=f"complete_{cb_suffix}_{notif_id}" if cb_suffix else f"complete_{notif_id}"),
           InlineKeyboardButton("⏰ Отложить", callback_data=f"snooze_{notif_id}"))
    
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=kb, parse_mode='Markdown')
        logger.info(f"Отправлено уведомление #{notif.get('num', notif_id)}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

# ==================== АВТОРИЗАЦИЯ ====================
@dp.callback_query_handler(lambda c: c.data == "start_auth", state='*')
async def start_auth(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🔑 Через код", callback_data="auth_method_code"), InlineKeyboardButton("🔓 Через токен", callback_data="auth_method_token"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_auth"))
    await bot.send_message(callback.from_user.id, "🔐 **Выберите способ авторизации:**", reply_markup=kb, parse_mode='Markdown')
    await AuthStates.waiting_for_auth_method.set()
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "auth_method_code", state=AuthStates.waiting_for_auth_method)
async def auth_method_code(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback.from_user.id, f"🔑 **Авторизация через код**\n\n1️⃣ [Нажмите для авторизации]({get_auth_url()})\n2️⃣ Войдите в аккаунт\n3️⃣ Разрешите доступ\n4️⃣ Скопируйте код из адресной строки (после `code=`)\n5️⃣ **Отправьте код сюда**\n\n⏰ 3 минуты", parse_mode='Markdown', disable_web_page_preview=True)
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "auth_method_token", state=AuthStates.waiting_for_auth_method)
async def auth_method_token(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback.from_user.id, f"🔓 **Авторизация через токен**\n\n1️⃣ [Получить токен](https://oauth.yandex.ru/authorize?response_type=token&client_id={CLIENT_ID})\n2️⃣ Войдите в аккаунт\n3️⃣ Разрешите доступ\n4️⃣ Скопируйте токен (после `access_token=`)\n5️⃣ **Отправьте токен сюда**\n\n⏰ 3 минуты", parse_mode='Markdown', disable_web_page_preview=True)
    await AuthStates.waiting_for_direct_token.set()
    await callback.answer()

@dp.message_handler(state=AuthStates.waiting_for_direct_token)
async def receive_direct_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    if len(token) < 20:
        await message.reply("❌ Токен слишком короткий", parse_mode='Markdown')
        return
    
    status = await message.reply("⏳ **Проверка токена...**", parse_mode='Markdown')
    save_user_token(message.from_user.id, token)
    ok, msg, disk = await check_yandex_access_with_test(message.from_user.id)
    
    if ok:
        disk.create_folder(config['backup_path'])
        cal = YandexCalendarAPI(token)
        cal_ok, cal_msg = await cal.test_connection()
        cal_status = f"\n📅 **Календарь:** {'✅' if cal_ok else '❌'} {cal_msg[:50]}"
        await status.delete()
        await message.reply(f"✅ **Токен действителен!**\n\n{msg}{cal_status}\n\n📁 Папка: `{config['backup_path']}`", parse_mode='Markdown')
        
        backups = await get_yadisk_backups(message.from_user.id)
        if backups:
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"), InlineKeyboardButton("❌ Нет", callback_data="decline_restore"))
            await message.reply(f"📦 **Найдено бэкапов:** {len(backups)}\nВосстановить?", reply_markup=kb, parse_mode='Markdown')
    else:
        delete_user_token(message.from_user.id)
        await status.edit_text(f"❌ **Токен недействителен!**\n\n{msg}", parse_mode='Markdown')
    await state.finish()

@dp.message_handler(state=AuthStates.waiting_for_yandex_code)
async def receive_code(message: types.Message, state: FSMContext):
    token = await get_access_token(message.text.strip())
    if not token:
        await message.reply("❌ **Ошибка авторизации!** Неверный код", parse_mode='Markdown')
        await state.finish()
        return
    
    save_user_token(message.from_user.id, token)
    await message.reply("✅ **Авторизация успешна!**", parse_mode='Markdown')
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "cancel_auth", state='*')
async def cancel_auth(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await cmd_start(callback.message, state)
    await callback.answer()

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================
async def reset_and_process(message: types.Message, state: FSMContext, handler):
    if await state.get_state():
        await state.finish()
        await message.reply("✅ **Предыдущая операция отменена**", parse_mode='Markdown')
    await handler(message, state)

@dp.message_handler(commands=['start'], state='*')
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        await message.reply("❌ Нет доступа")
        return
    
    token = get_user_token(message.from_user.id)
    if token:
        ok, msg = await check_yandex_access(message.from_user.id)
        backups = await get_yadisk_backups(message.from_user.id) if ok else []
        text = f"✅ **Доступ к Яндекс.Диску имеется!**\n\n🤖 v{BOT_VERSION}\n🌍 {config.get('timezone', 'Europe/Moscow')}\n📅 Синхр.: {'✅ Вкл' if get_calendar_sync_enabled() else '❌ Выкл'}"
        if backups:
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"), InlineKeyboardButton("❌ Нет", callback_data="decline_restore"))
            await message.reply(f"{text}\n\n📦 Бэкапов: {len(backups)}\nВосстановить?", reply_markup=kb, parse_mode='Markdown')
        else:
            await message.reply(text, parse_mode='Markdown')
    else:
        kb = InlineKeyboardMarkup(row_width=2).add(InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth"))
        await message.reply(f"👋 **Добро пожаловать!**\n\n🤖 v{BOT_VERSION}\n\n⚠️ **Нет доступа к Яндекс.Диску!**\nНажмите кнопку для авторизации:", reply_markup=kb, parse_mode='Markdown')
    
    await message.reply("👋 **Выберите действие:**", reply_markup=get_main_keyboard(), parse_mode='Markdown')

@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление", state='*')
async def add_notification_start(message: types.Message, state: FSMContext):
    await reset_and_process(message, state, lambda m,s: s.update() or m.reply("✏️ **Введите текст уведомления:**\n\n⏰ 3 минуты\n💡 /cancel для отмены", parse_mode='Markdown') or NotificationStates.waiting_for_text.set())

@dp.message_handler(state=NotificationStates.waiting_for_text)
async def get_notification_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await message.reply("⏱️ **Когда уведомить?**", reply_markup=get_time_type_keyboard(), parse_mode='Markdown')
    await NotificationStates.waiting_for_time_type.set()

@dp.callback_query_handler(lambda c: c.data == "back_to_text", state=NotificationStates.waiting_for_time_type)
async def back_to_text(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback.from_user.id, "✏️ **Введите новый текст:**", parse_mode='Markdown')
    await NotificationStates.waiting_for_text.set()
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('time_'), state=NotificationStates.waiting_for_time_type)
async def get_time_type(callback: types.CallbackQuery, state: FSMContext):
    tt = callback.data.replace('time_', '')
    await state.update_data(time_type=tt)
    
    prompts = {'hours':"⌛ **Часы:**", 'days':"📅 **Дни:**", 'months':"📆 **Месяцы:**", 'specific':"🗓️ **Дата (ДД.ММ.ГГГГ ЧЧ:ММ или ДД.ММ):**", 'every_day':"⏰ **Время (ЧЧ:ММ):**", 'weekdays':"📅 **Выберите дни недели**"}
    state_map = {'hours':NotificationStates.waiting_for_hours, 'days':NotificationStates.waiting_for_days, 'months':NotificationStates.waiting_for_months, 'specific':NotificationStates.waiting_for_specific_date, 'every_day':NotificationStates.waiting_for_every_day_time, 'weekdays':NotificationStates.waiting_for_weekdays}
    
    if tt == 'every_hour':
        await save_notification(callback, state, None, is_repeat=True, repeat_type='every_hour')
    elif tt == 'weekdays':
        await state.update_data(selected_weekdays=[])
        await bot.send_message(callback.from_user.id, "📅 **Выберите дни недели:**\n\nКогда закончите, нажмите «✅ Готово»", reply_markup=get_weekday_keyboard(), parse_mode='Markdown')
        await state_map[tt].set()
    elif tt in state_map:
        await bot.send_message(callback.from_user.id, f"{prompts[tt]}\n\n⏰ 3 минуты\n💡 /cancel", parse_mode='Markdown')
        await state_map[tt].set()
    await callback.answer()

async def save_notification(callback: types.CallbackQuery, state: FSMContext, value=None, is_repeat=False, repeat_type='no', edit_id=None):
    data = await state.get_data()
    edit_id = edit_id or data.get('edit_id')
    text = data['text']
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    
    if edit_id and edit_id in notifications:
        n = notifications[edit_id]
        n['text'] = text
        n['repeat_type'] = repeat_type
        n['notified'] = False
        n['is_completed'] = False
        n['is_repeat'] = is_repeat
        if repeat_type == 'every_hour':
            n['last_trigger'] = now.isoformat()
        elif repeat_type == 'every_day' and value:
            n['repeat_hour'], n['repeat_minute'] = value
            first = tz.localize(datetime(now.year, now.month, now.day, value[0], value[1]))
            if first <= now:
                first += timedelta(days=1)
            n['time'] = n['next_time'] = first.isoformat()
            n['last_trigger'] = (first - timedelta(days=1)).isoformat()
        elif repeat_type == 'weekdays' and value:
            n['repeat_hour'], n['repeat_minute'] = value[0], value[1]
            n['weekdays_list'] = data.get('weekdays_list', [])
            first = get_next_weekday(n['weekdays_list'], value[0], value[1], now)
            if first:
                n['time'] = n['next_time'] = first.isoformat()
        elif value:
            n['time'] = value.isoformat() if hasattr(value, 'isoformat') else value
        save_data()
        await sync_notification_to_calendar(edit_id, 'update')
        await bot.send_message(callback.from_user.id, f"✅ **Уведомление #{n.get('num', edit_id)} изменено!**\n📝 {text}", parse_mode='Markdown')
    else:
        nid = str(len(notifications) + 1)
        n = {'text': text, 'created': now.isoformat(), 'notified': False, 'is_completed': False, 'num': len(notifications)+1, 'repeat_type': repeat_type, 'is_repeat': is_repeat, 'repeat_count': 0}
        if repeat_type == 'every_hour':
            n['last_trigger'] = now.isoformat()
            n['time'] = now.isoformat()
        elif repeat_type == 'every_day' and value:
            n['repeat_hour'], n['repeat_minute'] = value
            first = tz.localize(datetime(now.year, now.month, now.day, value[0], value[1]))
            if first <= now:
                first += timedelta(days=1)
            n['time'] = n['next_time'] = first.isoformat()
            n['last_trigger'] = (first - timedelta(days=1)).isoformat()
        elif repeat_type == 'weekdays' and value:
            n['repeat_hour'], n['repeat_minute'] = value[0], value[1]
            n['weekdays_list'] = data.get('weekdays_list', [])
            first = get_next_weekday(n['weekdays_list'], value[0], value[1], now)
            n['time'] = n['next_time'] = first.isoformat() if first else now.isoformat()
        elif value:
            n['time'] = value.isoformat() if hasattr(value, 'isoformat') else value
        else:
            n['time'] = now.isoformat()
        
        notifications[nid] = n
        save_data()
        await sync_notification_to_calendar(nid, 'create')
        await bot.send_message(callback.from_user.id, f"✅ **Уведомление #{n['num']} создано!**\n📝 {text}", parse_mode='Markdown')
    
    await show_backup_notification(callback.message)
    await state.finish()

# Обработчики ввода времени
@dp.message_handler(state=[NotificationStates.waiting_for_hours, NotificationStates.waiting_for_days, NotificationStates.waiting_for_months])
async def handle_time_amount(message: types.Message, state: FSMContext):
    try:
        val = int(message.text)
        if val <= 0:
            raise ValueError
        data = await state.get_data()
        tt = data.get('time_type')
        delta = timedelta(hours=val) if tt == 'hours' else timedelta(days=val) if tt == 'days' else timedelta(days=val*30)
        await save_notification(message, state, get_current_time() + delta)
    except ValueError:
        await message.reply("❌ Введите положительное число", parse_mode='Markdown')

@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def handle_specific_date(message: types.Message, state: FSMContext):
    dt = parse_date(message.text)
    if not dt:
        await message.reply("❌ Неверный формат даты", parse_mode='Markdown')
        return
    if dt <= get_current_time():
        await message.reply("❌ Дата должна быть в будущем", parse_mode='Markdown')
        return
    await save_notification(message, state, dt)

@dp.message_handler(state=NotificationStates.waiting_for_every_day_time)
async def handle_every_day_time(message: types.Message, state: FSMContext):
    m = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await message.reply("❌ Формат ЧЧ:ММ (00-23:00-59)", parse_mode='Markdown')
        return
    await save_notification(message, state, (int(m.group(1)), int(m.group(2))), repeat_type='every_day')

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
    await bot.edit_message_reply_markup(callback.from_user.id, callback.message.message_id, reply_markup=get_weekday_keyboard(selected))
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "weekdays_done", state=NotificationStates.waiting_for_weekdays)
async def weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_weekdays', [])
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день!")
        return
    await state.update_data(weekdays_list=selected)
    await bot.send_message(callback.from_user.id, "⏰ **Введите время (ЧЧ:ММ):**\n\n⏰ 3 минуты", parse_mode='Markdown')
    await NotificationStates.waiting_for_weekday_time.set()
    await callback.answer()

@dp.message_handler(state=NotificationStates.waiting_for_weekday_time)
async def handle_weekday_time(message: types.Message, state: FSMContext):
    m = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await message.reply("❌ Формат ЧЧ:ММ", parse_mode='Markdown')
        return
    await save_notification(message, state, (int(m.group(1)), int(m.group(2))), repeat_type='weekdays')

# ==================== СПИСОК УВЕДОМЛЕНИЙ ====================
@dp.message_handler(lambda m: m.text == "📋 Список уведомлений", state='*')
async def list_notifications_handler(message: types.Message, state: FSMContext):
    await reset_and_process(message, state, lambda m,s: None)
    if not notifications:
        await message.reply("📭 **Нет активных уведомлений**", parse_mode='Markdown')
        return
    
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    for nid, n in sorted(notifications.items(), key=lambda x: int(x[0])):
        if n.get('is_completed'):
            continue
        
        rt = n.get('repeat_type', 'no')
        status_emoji = "🔄" if rt != 'no' else "⏳"
        status_text = "АКТИВНО" if rt != 'no' else "ОЖИДАЕТ"
        
        text = f"{status_emoji} **#{n.get('num', nid)}**\n📝 {n['text']}\n📊 {status_text}"
        
        if rt == 'every_hour':
            text += f"\n🔄 Каждый час"
        elif rt == 'every_day':
            text += f"\n🔄 Ежедневно в {n.get('repeat_hour',0):02d}:{n.get('repeat_minute',0):02d}"
        elif rt == 'weekdays':
            days = [WEEKDAYS_NAMES[d] for d in n.get('weekdays_list', [])]
            text += f"\n🔄 По {', '.join(days)} в {n.get('repeat_hour',0):02d}:{n.get('repeat_minute',0):02d}"
        elif n.get('time'):
            dt = datetime.fromisoformat(n['time'])
            if dt.tzinfo is None:
                dt = tz.localize(dt)
            text += f"\n⏰ {dt.strftime('%d.%m.%Y в %H:%M')}"
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_{nid}"), InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{nid}"))
        await message.reply(text, reply_markup=kb, parse_mode='Markdown')
    
    await message.reply(f"📊 **Всего:** {len(notifications)}", parse_mode='Markdown')

# ==================== РЕДАКТИРОВАНИЕ/УДАЛЕНИЕ ====================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_'), state='*')
async def edit_notification_menu(callback: types.CallbackQuery, state: FSMContext):
    nid = callback.data.replace('edit_', '')
    if nid not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    await state.update_data(edit_id=nid)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("✏️ Изменить текст", callback_data=f"do_edit_text_{nid}"), InlineKeyboardButton("⏰ Изменить время", callback_data=f"do_edit_time_{nid}"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
    await bot.send_message(callback.from_user.id, f"✏️ **Что изменить в #{notifications[nid].get('num', nid)}?**", reply_markup=kb, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('do_edit_text_'), state='*')
async def edit_text_handler(callback: types.CallbackQuery, state: FSMContext):
    nid = callback.data.replace('do_edit_text_', '')
    await state.update_data(edit_id=nid)
    await bot.send_message(callback.from_user.id, f"✏️ **Новый текст:**\n\nСтарый: {notifications[nid]['text']}\n\n⏰ 3 минуты", parse_mode='Markdown')
    await NotificationStates.waiting_for_edit_text.set()
    await callback.answer()

@dp.message_handler(state=NotificationStates.waiting_for_edit_text)
async def save_edited_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    nid = data.get('edit_id')
    if nid and nid in notifications:
        notifications[nid]['text'] = message.text
        save_data()
        await sync_notification_to_calendar(nid, 'update')
        await message.reply(f"✅ **Текст изменен!**", parse_mode='Markdown')
        await show_backup_notification(message)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('do_edit_time_'), state='*')
async def edit_time_handler(callback: types.CallbackQuery, state: FSMContext):
    nid = callback.data.replace('do_edit_time_', '')
    await state.update_data(edit_id=nid)
    await bot.send_message(callback.from_user.id, "⏱️ **Выберите новый период:**", reply_markup=get_time_type_keyboard(), parse_mode='Markdown')
    await NotificationStates.waiting_for_edit_time.set()
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data and (c.data.startswith('complete_') or c.data.startswith('complete_today_')))
async def handle_complete(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    today = parts[0] == 'complete_today'
    nid = parts[-1]
    
    if nid in notifications:
        n = notifications[nid]
        num = n.get('num', nid)
        
        if today and n.get('repeat_type') in ['every_day', 'weekdays']:
            n['last_trigger'] = get_current_time().isoformat()
            n['notified'] = False
            n['is_repeat'] = False
            n['repeat_count'] = 0
            save_data()
            await sync_notification_to_calendar(nid, 'update')
            await bot.send_message(callback.from_user.id, f"✅ **#{num} отмечено как выполненное на сегодня!**", parse_mode='Markdown')
        else:
            await sync_notification_to_calendar(nid, 'delete')
            del notifications[nid]
            # Перенумерация
            new_n = {}
            for i, (oid, on) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
                on['num'] = i
                new_n[str(i)] = on
            notifications.clear()
            notifications.update(new_n)
            save_data()
            await bot.send_message(callback.from_user.id, f"✅ **#{num} выполнено и удалено!**", parse_mode='Markdown')
        
        try:
            await callback.message.delete()
        except:
            pass
        await show_backup_notification(callback.message)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def handle_delete(callback: types.CallbackQuery):
    nid = callback.data.replace('delete_', '')
    if nid in notifications:
        num = notifications[nid].get('num', nid)
        await sync_notification_to_calendar(nid, 'delete')
        del notifications[nid]
        new_n = {}
        for i, (oid, on) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
            on['num'] = i
            new_n[str(i)] = on
        notifications.clear()
        notifications.update(new_n)
        save_data()
        await bot.send_message(callback.from_user.id, f"✅ **#{num} удалено!**", parse_mode='Markdown')
        await show_backup_notification(callback.message)
        try:
            await callback.message.delete()
        except:
            pass
    await callback.answer()

# ==================== ОТКЛАДЫВАНИЕ ====================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('snooze_'), state='*')
async def snooze_start(callback: types.CallbackQuery, state: FSMContext):
    nid = callback.data.replace('snooze_', '')
    if nid not in notifications:
        await callback.answer("Уведомление не найдено")
        return
    await state.update_data(snooze_notif_id=nid)
    await bot.send_message(callback.from_user.id, f"⏰ **Отложить уведомление**\n\n📝 {notifications[nid]['text']}", reply_markup=get_snooze_keyboard(nid), parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_select_"), state='*')
async def snooze_select(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.replace("snooze_select_", "").split("_")
    nid, val, unit = parts[0], int(parts[1]), parts[2]
    delta = timedelta(hours=val) if unit == "hours" else timedelta(days=val)
    new_time = get_current_time() + delta
    
    n = notifications[nid]
    n['time'] = new_time.isoformat()
    n['notified'] = False
    n['is_completed'] = False
    n['is_repeat'] = True
    n['repeat_count'] = n.get('repeat_count', 0) + 1
    n['repeat_type'] = 'no'
    save_data()
    await sync_notification_to_calendar(nid, 'update')
    await bot.send_message(callback.from_user.id, f"⏰ **Отложено на {val} {unit}**\n\n📝 {n['text']}\n🕐 {new_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
    await show_backup_notification(callback.message)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_custom_"), state='*')
async def snooze_custom(callback: types.CallbackQuery, state: FSMContext):
    nid = callback.data.replace("snooze_custom_", "")
    await state.update_data(snooze_notif_id=nid)
    kb = InlineKeyboardMarkup(row_width=2)
    for name, cb in [("⏰ В часах","hours"), ("📅 В днях","days"), ("📆 В месяцах","months"), ("🗓️ Конкретная дата","specific"), ("📅 Каждый день","every_day"), ("📆 По дням недели","weekdays")]:
        kb.add(InlineKeyboardButton(name, callback_data=f"snooze_custom_type_{cb}"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_snooze"))
    await bot.send_message(callback.from_user.id, "🎯 **Выберите тип откладывания:**", reply_markup=kb, parse_mode='Markdown')
    await SnoozeStates.waiting_for_snooze_type.set()
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("snooze_custom_type_"), state=SnoozeStates.waiting_for_snooze_type)
async def snooze_custom_type(callback: types.CallbackQuery, state: FSMContext):
    st = callback.data.replace("snooze_custom_type_", "")
    await state.update_data(snooze_custom_type=st)
    
    prompts = {'hours':"⌛ Часы:", 'days':"📅 Дни:", 'months':"📆 Месяцы:", 'specific':"🗓️ Дата (ДД.ММ.ГГГГ ЧЧ:ММ):", 'every_day':"⏰ Время (ЧЧ:ММ):"}
    states = {'hours':SnoozeStates.waiting_for_hours, 'days':SnoozeStates.waiting_for_days, 'months':SnoozeStates.waiting_for_months, 'specific':SnoozeStates.waiting_for_specific_date, 'every_day':SnoozeStates.waiting_for_every_day_time, 'weekdays':SnoozeStates.waiting_for_weekdays}
    
    if st == 'weekdays':
        await state.update_data(snooze_selected_weekdays=[])
        await bot.send_message(callback.from_user.id, "📅 **Выберите дни недели:**", reply_markup=get_weekday_keyboard(), parse_mode='Markdown')
        await states[st].set()
    elif st in states:
        await bot.send_message(callback.from_user.id, f"{prompts[st]}\n\n⏰ 3 минуты", parse_mode='Markdown')
        await states[st].set()
    await callback.answer()

# Обработчики откладывания (аналогичны основным)
@dp.message_handler(state=[SnoozeStates.waiting_for_hours, SnoozeStates.waiting_for_days, SnoozeStates.waiting_for_months])
async def snooze_amount(message: types.Message, state: FSMContext):
    try:
        val = int(message.text)
        if val <= 0:
            raise ValueError
        data = await state.get_data()
        st = data.get('snooze_custom_type')
        delta = timedelta(hours=val) if st == 'hours' else timedelta(days=val) if st == 'days' else timedelta(days=val*30)
        await apply_snooze(message, state, get_current_time() + delta)
    except ValueError:
        await message.reply("❌ Введите положительное число", parse_mode='Markdown')

@dp.message_handler(state=SnoozeStates.waiting_for_specific_date)
async def snooze_specific(message: types.Message, state: FSMContext):
    dt = parse_date(message.text)
    if not dt or dt <= get_current_time():
        await message.reply("❌ Неверная дата или дата в прошлом", parse_mode='Markdown')
        return
    await apply_snooze(message, state, dt)

@dp.message_handler(state=SnoozeStates.waiting_for_every_day_time)
async def snooze_every_day(message: types.Message, state: FSMContext):
    m = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await message.reply("❌ Формат ЧЧ:ММ", parse_mode='Markdown')
        return
    now = get_current_time()
    tz = pytz.timezone(config.get('timezone', 'Europe/Moscow'))
    dt = tz.localize(datetime(now.year, now.month, now.day, int(m.group(1)), int(m.group(2))))
    if dt <= now:
        dt += timedelta(days=1)
    await apply_snooze(message, state, dt)

@dp.callback_query_handler(lambda c: c.data.startswith('snooze_weekday_'), state=SnoozeStates.waiting_for_weekdays)
async def snooze_weekday_select(callback: types.CallbackQuery, state: FSMContext):
    day = int(callback.data.replace('snooze_weekday_', ''))
    data = await state.get_data()
    selected = data.get('snooze_selected_weekdays', [])
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    await state.update_data(snooze_selected_weekdays=selected)
    await bot.edit_message_reply_markup(callback.from_user.id, callback.message.message_id, reply_markup=get_weekday_keyboard(selected))
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "snooze_weekdays_done", state=SnoozeStates.waiting_for_weekdays)
async def snooze_weekdays_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('snooze_selected_weekdays', [])
    if not selected:
        await callback.answer("❌ Выберите хотя бы один день!")
        return
    await state.update_data(snooze_weekdays_list=selected)
    await bot.send_message(callback.from_user.id, "⏰ **Введите время (ЧЧ:ММ):**", parse_mode='Markdown')
    await SnoozeStates.waiting_for_weekday_time.set()
    await callback.answer()

@dp.message_handler(state=SnoozeStates.waiting_for_weekday_time)
async def snooze_weekday_time(message: types.Message, state: FSMContext):
    m = re.match(r'^(\d{1,2}):(\d{2})$', message.text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        await message.reply("❌ Формат ЧЧ:ММ", parse_mode='Markdown')
        return
    data = await state.get_data()
    weekdays = data.get('snooze_weekdays_list', [])
    dt = get_next_weekday(weekdays, int(m.group(1)), int(m.group(2)), get_current_time())
    if not dt:
        await message.reply("❌ Не удалось определить дату", parse_mode='Markdown')
        return
    await apply_snooze(message, state, dt)

async def apply_snooze(message: types.Message, state: FSMContext, new_time: datetime):
    data = await state.get_data()
    nid = data.get('snooze_notif_id')
    if nid not in notifications:
        await message.reply("❌ Уведомление не найдено", parse_mode='Markdown')
        await state.finish()
        return
    
    n = notifications[nid]
    n['time'] = new_time.isoformat()
    n['notified'] = False
    n['is_completed'] = False
    n['is_repeat'] = True
    n['repeat_count'] = n.get('repeat_count', 0) + 1
    n['repeat_type'] = 'no'
    save_data()
    await sync_notification_to_calendar(nid, 'update')
    await message.reply(f"✅ **Уведомление отложено!**\n\n📝 {n['text']}\n🕐 {new_time.strftime('%d.%m.%Y в %H:%M')}", parse_mode='Markdown')
    await show_backup_notification(message)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "cancel_snooze", state='*')
async def cancel_snooze(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(callback.from_user.id, "✅ **Откладывание отменено**", parse_mode='Markdown')
    await callback.answer()

# ==================== НАСТРОЙКИ ====================
@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state='*')
async def settings_menu_handler(message: types.Message, state: FSMContext):
    await reset_and_process(message, state, lambda m,s: None)
    status = "🔔 Вкл" if notifications_enabled else "🔕 Выкл"
    cal_status = "✅ Вкл" if get_calendar_sync_enabled() else "❌ Выкл"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton(f"🔔 Уведомления: {status}", callback_data="toggle_notifications"),
           InlineKeyboardButton(f"☁️ Синхр. с календарём: {cal_status}", callback_data="toggle_calendar_sync"),
           InlineKeyboardButton("📁 Папка на Диске", callback_data="select_backup_folder"),
           InlineKeyboardButton("🔢 Макс. бэкапов", callback_data="set_max_backups"),
           InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time"),
           InlineKeyboardButton("🌍 Часовой пояс", callback_data="set_timezone"),
           InlineKeyboardButton("🔑 Авторизация", callback_data="auth_yandex"),
           InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup_manual"),
           InlineKeyboardButton("📤 Восстановить", callback_data="restore_backup"),
           InlineKeyboardButton("🔄 Синхр. календарь→бот", callback_data="sync_calendar_to_bot"),
           InlineKeyboardButton("🔍 Проверить календарь", callback_data="check_calendar_connection"),
           InlineKeyboardButton("ℹ️ Информация", callback_data="info"))
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=kb, parse_mode='Markdown')

@dp.callback_query_handler(lambda c: c.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery, state: FSMContext):
    global notifications_enabled
    notifications_enabled = not notifications_enabled
    config['notifications_enabled'] = notifications_enabled
    save_data()
    await bot.send_message(callback.from_user.id, f"✅ Уведомления {'включены' if notifications_enabled else 'выключены'}", parse_mode='Markdown')
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "toggle_calendar_sync")
async def toggle_calendar_sync(callback: types.CallbackQuery, state: FSMContext):
    config['calendar_sync_enabled'] = not get_calendar_sync_enabled()
    save_data()
    await bot.send_message(callback.from_user.id, f"✅ Синхронизация {'включена' if get_calendar_sync_enabled() else 'выключена'}", parse_mode='Markdown')
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "set_max_backups")
async def set_max_backups(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"📊 Текущее: {config.get('max_backups', 5)}\n\nВведите число (1-20):\n\n⏰ 3 минуты", parse_mode='Markdown')
    await SettingsStates.waiting_for_max_backups.set()
    await callback.answer()

@dp.message_handler(state=SettingsStates.waiting_for_max_backups)
async def save_max_backups(message: types.Message, state: FSMContext):
    try:
        val = int(message.text)
        if 1 <= val <= 20:
            config['max_backups'] = val
            save_data()
            await message.reply(f"✅ Установлено: {val}", parse_mode='Markdown')
        else:
            await message.reply("❌ Число от 1 до 20", parse_mode='Markdown')
    except:
        await message.reply("❌ Введите число", parse_mode='Markdown')
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "set_check_time")
async def set_check_time(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"🕐 Текущее: {config.get('daily_check_time', '06:00')}\n\nВведите время (ЧЧ:ММ):\n\n⏰ 3 минуты", parse_mode='Markdown')
    await SettingsStates.waiting_for_check_time.set()
    await callback.answer()

@dp.message_handler(state=SettingsStates.waiting_for_check_time)
async def save_check_time(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%H:%M")
        config['daily_check_time'] = message.text
        save_data()
        await message.reply(f"✅ Время: {message.text}", parse_mode='Markdown')
    except:
        await message.reply("❌ Формат ЧЧ:ММ", parse_mode='Markdown')
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "set_timezone")
async def set_timezone(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=2)
    for name in TIMEZONES.keys():
        kb.add(InlineKeyboardButton(name, callback_data=f"tz_{name}"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_tz"))
    await bot.send_message(callback.from_user.id, f"🌍 **Часовой пояс**\n\nТекущий: {config.get('timezone', 'Europe/Moscow')}", reply_markup=kb, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("tz_"))
async def save_timezone(callback: types.CallbackQuery, state: FSMContext):
    tz_name = callback.data.replace("tz_", "")
    config['timezone'] = TIMEZONES.get(tz_name, 'Europe/Moscow')
    save_data()
    await bot.send_message(callback.from_user.id, f"✅ Часовой пояс: {tz_name}\n🕐 {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}", parse_mode='Markdown')
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_tz")
async def cancel_tz(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "select_backup_folder")
async def select_backup_folder(callback: types.CallbackQuery, state: FSMContext):
    token = get_user_token(callback.from_user.id)
    if not token:
        await bot.send_message(callback.from_user.id, "❌ Сначала авторизуйтесь", parse_mode='Markdown')
        await callback.answer()
        return
    
    disk = YandexDiskAPI(token)
    folders = disk.list_folders("/")
    kb = InlineKeyboardMarkup(row_width=1)
    for f in folders[:10]:
        kb.add(InlineKeyboardButton(f"📁 {f['name']}", callback_data=f"select_folder_{f['path'].replace('disk:','')}"))
    kb.add(InlineKeyboardButton("➕ Создать папку", callback_data="create_new_folder"), InlineKeyboardButton("❌ Отмена", callback_data="cancel_folder"))
    await bot.send_message(callback.from_user.id, f"📁 **Выберите папку**\n\nТекущая: {config['backup_path']}", reply_markup=kb, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("select_folder_"))
async def select_folder(callback: types.CallbackQuery):
    path = callback.data.replace("select_folder_", "")
    config['backup_path'] = path
    save_data()
    await bot.send_message(callback.from_user.id, f"✅ Папка: `{path}`", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "create_new_folder")
async def create_new_folder_prompt(callback: types.CallbackQuery, state: FSMContext):
    await bot.send_message(callback.from_user.id, "📁 **Введите название папки:**\n\n⏰ 3 минуты", parse_mode='Markdown')
    await SettingsStates.waiting_for_new_folder_name.set()
    await callback.answer()

@dp.message_handler(state=SettingsStates.waiting_for_new_folder_name)
async def create_new_folder(message: types.Message, state: FSMContext):
    token = get_user_token(message.from_user.id)
    if token:
        disk = YandexDiskAPI(token)
        new_path = f"{config['backup_path']}/{message.text.strip()}".replace('//', '/')
        if disk.create_folder(new_path):
            config['backup_path'] = new_path
            save_data()
            await message.reply(f"✅ Папка создана: `{new_path}`", parse_mode='Markdown')
        else:
            await message.reply("❌ Ошибка создания", parse_mode='Markdown')
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "cancel_folder")
async def cancel_folder(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "create_backup_manual")
async def manual_backup(callback: types.CallbackQuery):
    if not ADMIN_ID:
        await callback.answer("Ошибка")
        return
    msg = await bot.send_message(callback.from_user.id, "⏳ Создание бэкапа...", parse_mode='Markdown')
    success, _, loc = await create_backup(ADMIN_ID)
    await msg.edit_text(f"✅ Бэкап создан ({loc})" if success else "❌ Ошибка", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "restore_backup")
async def restore_backup_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("☁️ Из Яндекс.Диска", callback_data="restore_from_yadisk"), InlineKeyboardButton("📱 Из телефона", callback_data="restore_from_phone"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_restore"))
    await bot.send_message(callback.from_user.id, "📤 **Восстановление**\n\nВыберите источник:", reply_markup=kb, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "restore_from_yadisk")
async def restore_from_yadisk(callback: types.CallbackQuery):
    backups = await get_yadisk_backups(callback.from_user.id)
    if not backups:
        await bot.send_message(callback.from_user.id, "📭 Нет бэкапов", parse_mode='Markdown')
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(row_width=1)
    for b in backups[:10]:
        name = b['name'].replace('backup_', '').replace('.json', '')
        kb.add(InlineKeyboardButton(f"📦 {name}", callback_data=f"restore_backup_{b['name']}"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_restore"))
    await bot.send_message(callback.from_user.id, "📦 **Выберите бэкап:**", reply_markup=kb, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("restore_backup_"))
async def restore_selected(callback: types.CallbackQuery):
    backup_name = callback.data.replace("restore_backup_", "")
    msg = await bot.send_message(callback.from_user.id, "⏳ Восстановление...", parse_mode='Markdown')
    if await restore_from_yadisk_backup(backup_name, callback.from_user.id):
        await msg.edit_text(f"✅ Восстановлено!\n📝 Уведомлений: {len(notifications)}", parse_mode='Markdown')
        await show_backup_notification(callback.message)
    else:
        await msg.edit_text("❌ Ошибка восстановления", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "restore_from_phone")
async def restore_from_phone(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "📱 **Отправьте JSON файл бэкапа**\n\n⏰ 3 минуты", parse_mode='Markdown')
    await SettingsStates.waiting_for_upload_backup.set()
    await callback.answer()

@dp.message_handler(content_types=['document'], state=SettingsStates.waiting_for_upload_backup)
async def receive_backup_file(message: types.Message, state: FSMContext):
    try:
        file = await bot.get_file(message.document.file_id)
        data = json.loads((await bot.download_file(file.file_path)).read().decode('utf-8'))
        if 'notifications' in data:
            global notifications, config, calendar_sync
            notifications = data['notifications']
            for n in notifications.values():
                n.setdefault('is_completed', False)
            if 'config' in data:
                config = data['config']
            if 'calendar_sync' in data:
                calendar_sync = data['calendar_sync']
            save_data()
            save_calendar_sync()
            await message.reply(f"✅ Восстановлено!\n📝 Уведомлений: {len(notifications)}", parse_mode='Markdown')
            await show_backup_notification(message)
        else:
            await message.reply("❌ Неверный формат", parse_mode='Markdown')
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}", parse_mode='Markdown')
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "cancel_restore")
async def cancel_restore(callback: types.CallbackQuery, state: FSMContext):
    await settings_menu_handler(callback.message, state)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "check_calendar_connection")
async def check_calendar_connection(callback: types.CallbackQuery):
    token = get_user_token(callback.from_user.id)
    if not token:
        await bot.send_message(callback.from_user.id, "❌ Нет токена", parse_mode='Markdown')
        await callback.answer()
        return
    cal = YandexCalendarAPI(token)
    ok, msg = await cal.test_connection()
    if ok:
        await bot.send_message(callback.from_user.id, f"✅ {msg}", parse_mode='Markdown')
    else:
        await bot.send_message(callback.from_user.id, f"❌ {msg}", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "sync_calendar_to_bot")
async def sync_calendar_handler(callback: types.CallbackQuery):
    token = get_user_token(callback.from_user.id)
    if not token:
        await bot.send_message(callback.from_user.id, "❌ Нет токена", parse_mode='Markdown')
        await callback.answer()
        return
    msg = await bot.send_message(callback.from_user.id, "⏳ Синхронизация...", parse_mode='Markdown')
    cal = YandexCalendarAPI(token)
    cnt = await cal.sync_calendar_to_bot()
    await msg.edit_text(f"✅ Импортировано: {cnt}\n📝 Всего: {len(notifications)}", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    ok, msg = await check_yandex_access(callback.from_user.id) if get_user_token(callback.from_user.id) else (False, "Не авторизован")
    text = f"🤖 **v{BOT_VERSION}**\n📝 Уведомлений: {len(notifications)}\n💾 Макс. бэкапов: {config.get('max_backups',5)}\n🕐 Проверка: {config.get('daily_check_time','06:00')}\n🌍 Часовой пояс: {config.get('timezone','Europe/Moscow')}\n🔔 Уведомления: {'Вкл' if notifications_enabled else 'Выкл'}\n📅 Синхр.: {'Вкл' if get_calendar_sync_enabled() else 'Выкл'}\n🔑 Яндекс.Диск: {'✅' if ok else '❌'}"
    await bot.send_message(callback.from_user.id, text, parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data in ["offer_restore", "decline_restore"])
async def handle_restore_offer(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "offer_restore":
        await restore_backup_menu(callback)
    else:
        await bot.send_message(callback.from_user.id, "✅ Восстановление отменено", parse_mode='Markdown')
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_edit", state='*')
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(callback.from_user.id, "✅ Редактирование отменено", parse_mode='Markdown')
    await callback.answer()

@dp.message_handler(commands=['cancel'], state='*')
async def cancel_operation(message: types.Message, state: FSMContext):
    if await state.get_state():
        await state.finish()
        await message.reply("✅ Операция отменена", parse_mode='Markdown')
    else:
        await message.reply("❌ Нет активных операций", parse_mode='Markdown')

@dp.message_handler(commands=['version'])
async def show_version(message: types.Message):
    await message.reply(f"🤖 **Бот для уведомлений**\n📌 v{BOT_VERSION}\n📅 {BOT_VERSION_DATE}\n🕐 {BOT_VERSION_TIME}", parse_mode='Markdown')

@dp.message_handler(commands=['restart'])
async def restart_bot(message: types.Message):
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        await message.reply("🔄 Перезапуск...", parse_mode='Markdown')
        await asyncio.sleep(2)
        os._exit(0)

async def daily_check():
    while True:
        now = get_current_time()
        ch, cm = map(int, config.get('daily_check_time', '06:00').split(':'))
        target = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
        if now >= target:
            if ADMIN_ID:
                ok, _ = await check_yandex_access(ADMIN_ID)
                logger.info(f"Ежедневная проверка: {'✅' if ok else '❌'}")
            await asyncio.sleep(60)
        await asyncio.sleep(30)

async def on_startup(dp):
    init_folders()
    load_data()
    
    # Перенумерация
    new_n = {}
    for i, (nid, n) in enumerate(sorted(notifications.items(), key=lambda x: int(x[0])), 1):
        n['num'] = i
        new_n[str(i)] = n
    notifications.clear()
    notifications.update(new_n)
    save_data()
    
    logger.info(f"\n{'='*50}\n🤖 БОТ v{BOT_VERSION} ({BOT_VERSION_DATE})\n{'='*50}")
    logger.info(f"📝 Уведомлений: {len(notifications)}")
    logger.info(f"🔔 Уведомления: {'Вкл' if notifications_enabled else 'Выкл'}")
    logger.info(f"📅 Синхр.: {'Вкл' if get_calendar_sync_enabled() else 'Выкл'}")
    logger.info(f"🌍 Часовой пояс: {config.get('timezone', 'Europe/Moscow')}")
    logger.info(f"🕐 Текущее время: {get_current_time().strftime('%d.%m.%Y %H:%M:%S')}")
    logger.info(f"{'='*50}\n")
    
    asyncio.create_task(check_notifications())
    asyncio.create_task(daily_check())
    asyncio.create_task(sync_calendar_to_bot_task())
    logger.info("✅ Бот запущен!")

async def sync_calendar_to_bot_task():
    while True:
        try:
            if get_calendar_sync_enabled() and ADMIN_ID and get_user_token(ADMIN_ID):
                cal = YandexCalendarAPI(get_user_token(ADMIN_ID))
                await cal.sync_calendar_to_bot()
        except Exception as e:
            logger.error(f"Ошибка синхронизации: {e}")
        await asyncio.sleep(300)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)