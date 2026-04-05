import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

import aiohttp
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv

# Версия бота
BOT_VERSION = "2.0"
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

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Файлы для хранения данных
DATA_FILE = 'notifications.json'
CONFIG_FILE = 'config.json'
TOKEN_FILE = 'user_tokens.json'  # Изменено для поддержки нескольких пользователей
BACKUP_DIR = 'backups'

# Глобальные переменные
notifications: Dict = {}
config: Dict = {}
user_tokens: Dict[int, str] = {}  # user_id -> token
yandex_disk_cache: Dict[int, Any] = {}  # Кэш для API
notifications_enabled = True

# URL для Яндекс.Диск API
YANDEX_API_BASE = "https://cloud-api.yandex.net/v1/disk"
YANDEX_OAUTH_URL = "https://oauth.yandex.ru/authorize"


def get_auth_url() -> str:
    """Получение URL для авторизации в Яндекс"""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI
    }
    return f"{YANDEX_OAUTH_URL}?{urlencode(params)}"


async def get_access_token(auth_code: str) -> Optional[str]:
    """Получение access token по коду авторизации"""
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


# Класс для работы с Яндекс.Диском через API (обновлен)
class YandexDiskAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = YANDEX_API_BASE
        self.headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }
    
    def check_access(self):
        """Проверяет доступ к Яндекс.Диску и права на запись"""
        try:
            url = f"{self.base_url}/"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                return False, "Нет доступа к диску"
            
            test_folder = "/_test_permissions_" + datetime.now().strftime('%Y%m%d_%H%M%S')
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
    
    def create_folder(self, folder_path):
        """Создает папку на Яндекс.Диске"""
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path}
            response = requests.put(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 201, 202]
        except Exception as e:
            print(f"Ошибка создания папки: {e}")
            return False
    
    def upload_file(self, local_path, remote_path):
        """Загружает файл на Яндекс.Диск"""
        try:
            url = f"{self.base_url}/resources/upload"
            params = {"path": remote_path, "overwrite": True}
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
        """Получает список файлов в папке"""
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
        """Удаляет файл на Яндекс.Диске"""
        try:
            url = f"{self.base_url}/resources"
            params = {"path": remote_path, "permanently": True}
            response = requests.delete(url, headers=self.headers, params=params, timeout=10)
            return response.status_code in [200, 202, 204]
        except Exception as e:
            print(f"Ошибка удаления файла: {e}")
            return False
    
    def download_file(self, remote_path, local_path):
        """Скачивает файл с Яндекс.Диска"""
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


# Состояния FSM (исправлены)
class AuthStates(StatesGroup):
    waiting_for_yandex_code = State()


class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time_type = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()


class SettingsStates(StatesGroup):
    waiting_for_backup_path = State()
    waiting_for_max_backups = State()
    waiting_for_check_time = State()


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
            'notifications_enabled': True
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
    
    # Загружаем сохраненные токены пользователей
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                token_data = json.load(f)
                # Конвертируем ключи из строк в int
                user_tokens = {int(k): v for k, v in token_data.items()}
        except:
            user_tokens = {}


def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(notifications, f, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def save_user_token(user_id: int, token: str):
    """Сохраняет токен пользователя"""
    global user_tokens
    user_tokens[user_id] = token
    # Сохраняем все токены
    with open(TOKEN_FILE, 'w') as f:
        json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)


def get_user_token(user_id: int) -> Optional[str]:
    """Получает токен пользователя"""
    return user_tokens.get(user_id)


def delete_user_token(user_id: int):
    """Удаляет токен пользователя"""
    if user_id in user_tokens:
        del user_tokens[user_id]
        with open(TOKEN_FILE, 'w') as f:
            json.dump({str(k): v for k, v in user_tokens.items()}, f, indent=2)


# Проверка доступа к Яндекс.Диску для пользователя
async def check_yandex_access(user_id: int) -> tuple:
    token = get_user_token(user_id)
    
    if not token:
        return False, "Нет токена авторизации"
    
    try:
        yandex_disk = YandexDiskAPI(token)
        access, message = yandex_disk.check_access()
        
        if access:
            # Создаем папку для бэкапов если её нет
            yandex_disk.create_folder(config['backup_path'])
            print(f"✅ Доступ к Яндекс.Диску для user {user_id} успешно получен")
        else:
            print(f"❌ Нет доступа к Яндекс.Диску для user {user_id}: {message}")
        
        return access, message
    except Exception as e:
        print(f"Ошибка доступа к Яндекс.Диску: {e}")
        return False, str(e)


# Создание бэкапа
async def create_backup(user_id: int = None, show_message=True) -> tuple:
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = Path(BACKUP_DIR) / f'backup_{timestamp}.json'
        
        backup_data = {
            'notifications': notifications,
            'config': config,
            'timestamp': timestamp
        }
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        # Если есть пользователь с токеном, загружаем на его диск
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
                        print(f"✅ Бэкап успешно создан: {backup_file}")
                        return True, backup_file
                    else:
                        print("❌ Ошибка загрузки на Яндекс.Диск")
                        return False, None
                else:
                    print("❌ Яндекс.Диск не доступен")
                    return False, None
        return False, None
    except Exception as e:
        print(f"Ошибка создания бэкапа: {e}")
        return False, None


async def cleanup_old_backups(user_id: int = None):
    """Оставляет только последние 5 бэкапов"""
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
                        print(f"Удален старый бэкап: {old_file['name']}")
        
        local_backups = sorted(Path(BACKUP_DIR).glob('backup_*.json'))
        max_backups = config.get('max_backups', 5)
        for old_backup in local_backups[:-max_backups]:
            old_backup.unlink()
            print(f"Удален локальный бэкап: {old_backup}")
    except Exception as e:
        print(f"Ошибка очистки старых бэкапов: {e}")


# Проверка уведомлений
async def check_notifications():
    global notifications_enabled
    while True:
        if notifications_enabled:
            now = datetime.now()
            
            for notif_id, notif in list(notifications.items()):
                notify_time = datetime.fromisoformat(notif['time'])
                
                if now >= notify_time and not notif.get('notified', False):
                    keyboard = InlineKeyboardMarkup(row_width=2)
                    keyboard.add(
                        InlineKeyboardButton("✅ Удалить", callback_data=f"delete_{notif_id}"),
                        InlineKeyboardButton("⏰ Отложить на час", callback_data=f"snooze_{notif_id}_1")
                    )
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔔 НАПОМИНАНИЕ!\n\n📝 {notif['text']}",
                        reply_markup=keyboard
                    )
                    
                    notifications[notif_id]['notified'] = True
                    save_data()
                    
        await asyncio.sleep(30)


# Ежедневная проверка
checking_daily = False
async def daily_check():
    global checking_daily
    while True:
        now = datetime.now()
        check_time = config.get('daily_check_time', '06:00')
        check_hour, check_minute = map(int, check_time.split(':'))
        target_time = now.replace(hour=check_hour, minute=check_minute, second=0, microsecond=0)
        
        if now >= target_time and not checking_daily:
            checking_daily = True
            
            # Проверяем доступ для администратора
            if ADMIN_ID:
                access, message = await check_yandex_access(ADMIN_ID)
                if not access:
                    print(f"❌ Ежедневная проверка: нет доступа к Яндекс.Диску - {message}")
                else:
                    print("✅ Ежедневная проверка: доступ к Яндекс.Диску есть")
            
            await asyncio.sleep(60)
            checking_daily = False
        
        await asyncio.sleep(30)


# Основная клавиатура
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


# Команда /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
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
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})",
                parse_mode='Markdown'
            )
        else:
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton("🔑 Авторизовать Яндекс.Диск", callback_data="auth_yandex"))
            
            await message.reply(
                f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
                f"Причина: {access_message}\n\n"
                f"Для работы бэкапов необходимо авторизоваться.\n\n"
                f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
    else:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔑 Авторизовать Яндекс.Диск", callback_data="auth_yandex"))
        
        await message.reply(
            f"👋 **Добро пожаловать!**\n\n"
            f"🤖 **Версия бота:** v{BOT_VERSION} ({BOT_VERSION_DATE})\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов необходимо авторизоваться.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    
    await message.reply(
        "👋 **Выберите действие:**",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )


# Авторизация Яндекс.Диска
@dp.callback_query_handler(lambda c: c.data == "auth_yandex")
async def auth_yandex(callback: types.CallbackQuery):
    auth_url = get_auth_url()
    
    await bot.send_message(
        callback.from_user.id,
        f"🔑 **Авторизация Яндекс.Диска**\n\n"
        f"1️⃣ Перейдите по ссылке:\n"
        f"`{auth_url}`\n\n"
        f"2️⃣ Войдите в аккаунт и разрешите доступ\n\n"
        f"3️⃣ Скопируйте код из адресной строки (часть после `code=`)\n\n"
        f"4️⃣ **Отправьте полученный код сюда** (просто текстом)\n\n"
        f"📝 Пример: `1234567890`",
        parse_mode='Markdown'
    )
    await AuthStates.waiting_for_yandex_code.set()
    await callback.answer()


@dp.message_handler(state=AuthStates.waiting_for_yandex_code)
async def receive_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    if not code:
        await message.reply("❌ **Ошибка!** Отправьте код авторизации.", parse_mode='Markdown')
        return
    
    await message.reply("⏳ **Получение токена...**", parse_mode='Markdown')
    
    token = await get_access_token(code)
    
    if token:
        save_user_token(user_id, token)
        
        access, access_message = await check_yandex_access(user_id)
        
        if access:
            await message.reply(
                f"✅ **Авторизация успешна!**\n\n"
                f"Статус: {access_message}\n"
                f"Теперь бэкапы будут сохраняться на Яндекс.Диск.",
                parse_mode='Markdown'
            )
        else:
            await message.reply(
                f"⚠️ **Токен получен, но доступ ограничен!**\n\n"
                f"Причина: {access_message}\n\n"
                f"Проверьте права доступа приложения.",
                parse_mode='Markdown'
            )
    else:
        await message.reply(
            f"❌ **Ошибка авторизации!**\n\n"
            f"Возможные причины:\n"
            f"- Неверный код\n"
            f"- Код уже использован\n"
            f"- Проблемы с соединением\n\n"
            f"Попробуйте снова нажав кнопку авторизации.",
            parse_mode='Markdown'
        )
    
    await state.finish()
    # Показываем главное меню
    await cmd_start(message)


# Добавление уведомления (исправлено)
@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление")
async def add_notification_start(message: types.Message, state: FSMContext):
    await state.finish()  # Сбрасываем предыдущее состояние
    await message.reply("✏️ **Введите текст уведомления:**", parse_mode='Markdown')
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
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="time_specific")
    )
    
    await message.reply("⏱️ **Через сколько уведомить?**", reply_markup=keyboard, parse_mode='Markdown')
    await NotificationStates.waiting_for_time_type.set()


@dp.callback_query_handler(lambda c: c.data.startswith('time_'), state=NotificationStates.waiting_for_time_type)
async def get_time_type(callback: types.CallbackQuery, state: FSMContext):
    time_type = callback.data.replace('time_', '')
    await state.update_data(time_type=time_type)
    
    if time_type == 'hours':
        await bot.send_message(callback.from_user.id, "⌛ **Введите количество часов:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_hours.set()
    elif time_type == 'days':
        await bot.send_message(callback.from_user.id, "📅 **Введите количество дней:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_days.set()
    elif time_type == 'months':
        await bot.send_message(callback.from_user.id, "📆 **Введите количество месяцев:**", parse_mode='Markdown')
        await NotificationStates.waiting_for_months.set()
    elif time_type == 'specific':
        await bot.send_message(callback.from_user.id, "🗓️ **Введите дату** (ГГГГ-ММ-ДД ЧЧ:ММ):\nПример: `2025-12-31 23:59`", parse_mode='Markdown')
        await NotificationStates.waiting_for_specific_date.set()
    
    await callback.answer()


async def save_notification(message: types.Message, state: FSMContext, notify_time: datetime):
    """Сохраняет уведомление и создает бэкап"""
    data = await state.get_data()
    notif_id = str(int(datetime.now().timestamp()))
    
    notifications[notif_id] = {
        'text': data['text'],
        'time': notify_time.isoformat(),
        'created': datetime.now().isoformat(),
        'notified': False
    }
    
    save_data()
    
    await message.reply(
        f"✅ **Уведомление создано!**\n"
        f"📝 {data['text']}\n"
        f"⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode='Markdown'
    )
    
    # Создаем бэкап
    if ADMIN_ID:
        success, _ = await create_backup(ADMIN_ID)
        if success:
            msg = await message.reply("✅ **Бэкап создан на Яндекс.Диске**")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("⚠️ **Бэкап не создан** (нет доступа к Яндекс.Диску)")
    
    await state.finish()


@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if hours <= 0:
            await message.reply("❌ **Ошибка!** Введите положительное число часов.", parse_mode='Markdown')
            return
        notify_time = datetime.now() + timedelta(hours=hours)
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
        notify_time = datetime.now() + timedelta(days=days)
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
        notify_time = datetime.now() + timedelta(days=days)
        await save_notification(message, state, notify_time)
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число месяцев.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def set_specific_date(message: types.Message, state: FSMContext):
    try:
        notify_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        
        if notify_time <= datetime.now():
            await message.reply("❌ **Ошибка!** Дата должна быть в будущем!", parse_mode='Markdown')
            return
        
        await save_notification(message, state, notify_time)
    except ValueError:
        await message.reply("❌ **Ошибка!** Неверный формат даты!\nПример: `2025-12-31 23:59`", parse_mode='Markdown')


# Список уведомлений
@dp.message_handler(lambda m: m.text == "📋 Список уведомлений")
async def list_notifications(message: types.Message):
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    text = "📋 **ВАШИ УВЕДОМЛЕНИЯ:**\n\n"
    for notif_id, notif in list(notifications.items())[:20]:
        notify_time = datetime.fromisoformat(notif['time'])
        status = "✅" if notif.get('notified') else "⏳"
        text += f"{status} `{notify_time.strftime('%d.%m.%Y %H:%M')}`\n"
        text += f"📝 {notif['text'][:50]}\n"
        text += f"🆔 ID: `{notif_id}`\n\n"
    
    text += "\n💡 **Чтобы удалить уведомление:**\nОтправьте команду:\n`/delete_уведомления_ID`"
    
    await message.reply(text, parse_mode='Markdown')


# Удаление уведомления
@dp.message_handler(lambda m: m.text and m.text.startswith('/delete_'))
async def delete_notification(message: types.Message):
    notif_id = message.text.replace('/delete_', '')
    
    if notif_id in notifications:
        del notifications[notif_id]
        save_data()
        
        await message.reply("✅ **Уведомление удалено!**", parse_mode='Markdown')
        
        if ADMIN_ID:
            success, _ = await create_backup(ADMIN_ID)
            if success:
                msg = await message.reply("✅ **Бэкап создан на Яндекс.Диске**")
                await asyncio.sleep(60)
                await msg.delete()
            else:
                await message.reply("⚠️ **Бэкап не создан** (нет доступа к Яндекс.Диску)")
    else:
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')


# Обработка кнопок уведомлений
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def handle_delete_notification(callback: types.CallbackQuery):
    notif_id = callback.data.replace('delete_', '')
    
    if notif_id in notifications:
        del notifications[notif_id]
        save_data()
        
        await bot.edit_message_text(
            "✅ **Уведомление удалено**",
            callback.from_user.id,
            callback.message.message_id,
            parse_mode='Markdown'
        )
        
        if ADMIN_ID:
            success, _ = await create_backup(ADMIN_ID)
            if success:
                msg = await bot.send_message(callback.from_user.id, "✅ **Бэкап создан**")
                await asyncio.sleep(60)
                await msg.delete()
    else:
        await callback.answer("Уведомление уже удалено")
    
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
        new_time = datetime.now() + timedelta(hours=hours)
        notifications[notif_id]['time'] = new_time.isoformat()
        notifications[notif_id]['notified'] = False
        save_data()
        
        await bot.edit_message_text(
            f"⏰ **Уведомление отложено на {hours} час(ов)**\n"
            f"Новое время: {new_time.strftime('%H:%M %d.%m.%Y')}",
            callback.from_user.id,
            callback.message.message_id,
            parse_mode='Markdown'
        )
    
    await callback.answer()


# Настройки
@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def settings_menu(message: types.Message):
    global notifications_enabled
    
    status_text = "🔕 Выкл" if not notifications_enabled else "🔔 Вкл"
    status_emoji = "🔕" if not notifications_enabled else "🔔"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(f"{status_emoji} Уведомления: {status_text}", callback_data="toggle_notifications"),
        InlineKeyboardButton("📁 Путь на Яндекс.Диске", callback_data="set_backup_path")
    )
    keyboard.add(
        InlineKeyboardButton("🔢 Максимум бэкапов", callback_data="set_max_backups"),
        InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time")
    )
    keyboard.add(
        InlineKeyboardButton("🔑 Авторизация Яндекс.Диска", callback_data="auth_yandex"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="info")
    )
    
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=keyboard, parse_mode='Markdown')


@dp.callback_query_handler(lambda c: c.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery):
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
    
    # Обновляем меню настроек
    await settings_menu(callback.message)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "set_backup_path")
async def set_backup_path(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"📁 **Текущий путь:** `{config['backup_path']}`\n\n"
        "**Введите новый путь на Яндекс.Диске** (например: `/MyBackups`):",
        parse_mode='Markdown'
    )
    await SettingsStates.waiting_for_backup_path.set()
    await callback.answer()


@dp.message_handler(state=SettingsStates.waiting_for_backup_path)
async def save_backup_path(message: types.Message, state: FSMContext):
    config['backup_path'] = message.text
    save_data()
    
    if ADMIN_ID:
        token = get_user_token(ADMIN_ID)
        if token:
            yandex_disk = YandexDiskAPI(token)
            access, _ = await check_yandex_access(ADMIN_ID)
            if access:
                yandex_disk.create_folder(config['backup_path'])
    
    await message.reply(f"✅ **Путь сохранен:** `{config['backup_path']}`", parse_mode='Markdown')
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "set_max_backups")
async def set_max_backups(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"📊 **Текущее количество бэкапов:** `{config.get('max_backups', 5)}`\n\n"
        "**Введите новое число** (от 1 до 20):",
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
            await message.reply(f"✅ **Максимум бэкапов установлен:** `{max_backups}`", parse_mode='Markdown')
        else:
            await message.reply("❌ **Ошибка!** Число должно быть от 1 до 20", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "set_check_time")
async def set_check_time(callback: types.CallbackQuery):
    await bot.send_message(
        callback.from_user.id,
        f"🕐 **Текущее время проверки:** `{config.get('daily_check_time', '06:00')}`\n\n"
        "**Введите новое время** в формате `ЧЧ:ММ` (например: `08:30`):",
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
        await message.reply(f"✅ **Время проверки установлено:** `{message.text}`", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Ошибка!** Неверный формат времени. Используйте `ЧЧ:ММ`", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    access = False
    access_message = "Не авторизован"
    
    if ADMIN_ID:
        access, access_message = await check_yandex_access(ADMIN_ID)
    
    info = f"""
📊 **СТАТИСТИКА БОТА**

🤖 **Версия:** v{BOT_VERSION} ({BOT_VERSION_DATE})

📝 **Активных уведомлений:** `{len(notifications)}`
💾 **Максимум бэкапов:** `{config.get('max_backups', 5)}`
📁 **Путь бэкапов:** `{config['backup_path']}`
🕐 **Проверка в:** `{config.get('daily_check_time', '06:00')}`
🔔 **Уведомления:** `{'Включены' if notifications_enabled else 'Выключены'}`

📦 **Локальных бэкапов:** `{len(list(Path(BACKUP_DIR).glob('backup_*.json')))}`

🔑 **Яндекс.Диск:** `{'✅ Доступен' if access else '❌ ' + access_message}`
"""
    await bot.send_message(callback.from_user.id, info, parse_mode='Markdown')
    await callback.answer()


# Создание бэкапа вручную
@dp.message_handler(lambda m: m.text == "💾 Создать бэкап")
async def manual_backup(message: types.Message):
    if not ADMIN_ID:
        await message.reply("❌ Ошибка: ADMIN_ID не задан", parse_mode='Markdown')
        return
    
    await message.reply("⏳ **Создание бэкапа...**", parse_mode='Markdown')
    success, backup_file = await create_backup(ADMIN_ID)
    if success:
        msg = await message.reply("✅ **Бэкап создан на Яндекс.Диске**")
        await asyncio.sleep(60)
        await msg.delete()
    else:
        await message.reply("⚠️ **Бэкап не создан!**\nПроверьте доступ к Яндекс.Диску в настройках.", parse_mode='Markdown')


# Команда /restart
@dp.message_handler(commands=['restart'])
async def restart_bot(message: types.Message):
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        await message.reply("🔄 **Перезапуск бота...**", parse_mode='Markdown')
        await asyncio.sleep(2)
        os._exit(0)


# Команда /version
@dp.message_handler(commands=['version'])
async def show_version(message: types.Message):
    await message.reply(
        f"🤖 **Бот для уведомлений**\n"
        f"📌 **Версия:** v{BOT_VERSION}\n"
        f"📅 **Дата:** {BOT_VERSION_DATE}",
        parse_mode='Markdown'
    )


# Запуск бота
async def on_startup(dp):
    init_folders()
    load_data()
    
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
    print(f"{'='*50}\n")
    
    asyncio.create_task(check_notifications())
    asyncio.create_task(daily_check())
    print("✅ Бот успешно запущен!")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)