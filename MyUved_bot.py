import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv
import requests

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
YANDEX_TOKEN = os.getenv('YANDEX_TOKEN')
BACKUP_FOLDER = os.getenv('BACKUP_FOLDER', 'MyUved_backups')

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Файлы для хранения данных
DATA_FILE = 'notifications.json'
CONFIG_FILE = 'config.json'
BACKUP_DIR = 'backups'

# Глобальные переменные
yandex_session = None
notifications: Dict = {}
config: Dict = {}
checking_daily = False


# Класс для работы с Яндекс.Диском через API
class YandexDiskAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://cloud-api.yandex.net/v1/disk"
        self.headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }
    
    def check_access(self):
        """Проверяет доступ к Яндекс.Диску"""
        try:
            url = f"{self.base_url}/"
            response = requests.get(url, headers=self.headers)
            return response.status_code == 200
        except Exception as e:
            print(f"Ошибка проверки доступа: {e}")
            return False
    
    def create_folder(self, folder_path):
        """Создает папку на Яндекс.Диске"""
        try:
            url = f"{self.base_url}/resources"
            params = {"path": folder_path}
            response = requests.put(url, headers=self.headers, params=params)
            return response.status_code in [200, 201, 202]
        except Exception as e:
            print(f"Ошибка создания папки: {e}")
            return False
    
    def upload_file(self, local_path, remote_path):
        """Загружает файл на Яндекс.Диск"""
        try:
            # Получаем URL для загрузки
            url = f"{self.base_url}/resources/upload"
            params = {"path": remote_path, "overwrite": True}
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.status_code == 200:
                upload_url = response.json().get("href")
                # Загружаем файл
                with open(local_path, 'rb') as f:
                    upload_response = requests.put(upload_url, files={"file": f})
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
            response = requests.get(url, headers=self.headers, params=params)
            
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
            response = requests.delete(url, headers=self.headers, params=params)
            return response.status_code in [200, 202, 204]
        except Exception as e:
            print(f"Ошибка удаления файла: {e}")
            return False
    
    def download_file(self, remote_path, local_path):
        """Скачивает файл с Яндекс.Диска"""
        try:
            url = f"{self.base_url}/resources/download"
            params = {"path": remote_path}
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.status_code == 200:
                download_url = response.json().get("href")
                download_response = requests.get(download_url)
                
                with open(local_path, 'wb') as f:
                    f.write(download_response.content)
                return True
            return False
        except Exception as e:
            print(f"Ошибка скачивания файла: {e}")
            return False


# Инициализация Яндекс.Диск API
yandex_disk = None


# Инициализация папок
def init_folders():
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w') as f:
            json.dump({}, f)
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            'backup_path': BACKUP_FOLDER,
            'max_backups': 5,
            'daily_check_time': '06:00'
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f)


def load_data():
    global notifications, config
    with open(DATA_FILE, 'r') as f:
        notifications = json.load(f)
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)


def save_data():
    with open(DATA_FILE, 'w') as f:
        json.dump(notifications, f, indent=2, ensure_ascii=False)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# Проверка доступа к Яндекс.Диску
async def check_yandex_access() -> bool:
    global yandex_disk
    try:
        if not YANDEX_TOKEN:
            print("YANDEX_TOKEN не найден в .env файле")
            return False
        
        yandex_disk = YandexDiskAPI(YANDEX_TOKEN)
        access = yandex_disk.check_access()
        
        if access:
            # Создаем папку для бэкапов если её нет
            yandex_disk.create_folder(config['backup_path'])
            print("Доступ к Яндекс.Диску успешно получен")
        
        return access
    except Exception as e:
        print(f"Ошибка доступа к Яндекс.Диску: {e}")
        return False


# Создание бэкапа
async def create_backup() -> tuple:
    try:
        # Создаем имя для бэкапа с временной меткой
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = Path(BACKUP_DIR) / f'backup_{timestamp}.json'
        
        # Сохраняем текущие данные в бэкап
        backup_data = {
            'notifications': notifications,
            'config': config,
            'timestamp': timestamp
        }
        
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)
        
        # Загружаем на Яндекс.Диск
        if yandex_disk and yandex_disk.check_access():
            remote_path = f"{config['backup_path']}/backup_{timestamp}.json"
            
            # Создаем папку если её нет
            yandex_disk.create_folder(config['backup_path'])
            
            # Загружаем файл
            if yandex_disk.upload_file(str(backup_file), remote_path):
                # Удаляем старые бэкапы
                await cleanup_old_backups()
                print(f"✅ Бэкап успешно создан: {backup_file}")
                return True, backup_file
            else:
                print("❌ Ошибка загрузки на Яндекс.Диск")
                return False, None
        else:
            print("❌ Яндекс.Диск не доступен")
            return False, None
    except Exception as e:
        print(f"Ошибка создания бэкапа: {e}")
        return False, None


async def cleanup_old_backups():
    """Оставляет только последние 5 бэкапов"""
    try:
        if yandex_disk and yandex_disk.check_access():
            # Получаем список файлов в папке бэкапов
            files = yandex_disk.list_files(config['backup_path'])
            backup_files = [f for f in files if f['name'].startswith('backup_')]
            
            # Сортируем по дате и оставляем только max_backups
            backup_files.sort(key=lambda x: x['name'], reverse=True)
            max_backups = config.get('max_backups', 5)
            
            for old_file in backup_files[max_backups:]:
                remote_path = f"{config['backup_path']}/{old_file['name']}"
                yandex_disk.delete_file(remote_path)
                print(f"Удален старый бэкап: {old_file['name']}")
        
        # Также чистим локальные бэкапы
        local_backups = sorted(Path(BACKUP_DIR).glob('backup_*.json'))
        max_backups = config.get('max_backups', 5)
        for old_backup in local_backups[:-max_backups]:
            old_backup.unlink()
            print(f"Удален локальный бэкап: {old_backup}")
    except Exception as e:
        print(f"Ошибка очистки старых бэкапов: {e}")


# Проверка уведомлений
async def check_notifications():
    while True:
        now = datetime.now()
        to_remove = []
        
        for notif_id, notif in notifications.items():
            notify_time = datetime.fromisoformat(notif['time'])
            
            if now >= notify_time and not notif.get('notified', False):
                # Отправляем уведомление
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
                
        await asyncio.sleep(30)  # Проверяем каждые 30 секунд


# Ежедневная проверка в 6:00
async def daily_check():
    global checking_daily
    while True:
        now = datetime.now()
        target_time = now.replace(hour=6, minute=0, second=0, microsecond=0)
        
        if now >= target_time and not checking_daily:
            checking_daily = True
            
            # Проверяем доступ к Яндекс.Диску
            if await check_yandex_access():
                msg = await bot.send_message(ADMIN_ID, "✅ Доступ к Яндекс.Диску имеется")
                await asyncio.sleep(5)
                await msg.delete()
                
                # Предлагаем обновить базу
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("🔄 Обновить базу", callback_data="update_database"))
                await bot.send_message(
                    ADMIN_ID,
                    "📦 Хотите обновить базу уведомлений из последнего бэкапа?",
                    reply_markup=keyboard
                )
            else:
                # Инструкция по получению токена
                instruction = """
❌ **НЕТ ДОСТУПА К ЯНДЕКС.ДИСКУ**

📝 **ИНСТРУКЦИЯ ПО ПОЛУЧЕНИЮ YANDEX_TOKEN:**

1️⃣ Перейдите по ссылке:
`https://oauth.yandex.ru/authorize?response_type=token&client_id=5b91097685e2419eb0da38c4b4fc345b`

2️⃣ Войдите в свой Яндекс аккаунт

3️⃣ Разрешите доступ приложению

4️⃣ Скопируйте полученный токен из адресной строки браузера (часть после #access_token=)

5️⃣ Добавьте токен в файл `.env`:
`YANDEX_TOKEN=ваш_токен_здесь`

6️⃣ Перезапустите бота командой: `/restart`
"""
                await bot.send_message(ADMIN_ID, instruction, parse_mode='Markdown')
            
            await asyncio.sleep(60)
            checking_daily = False
        
        await asyncio.sleep(30)


# Команда /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("❌ У вас нет доступа к этому боту")
        return
    
    # Проверяем доступ к Яндекс.Диску
    if await check_yandex_access():
        msg = await message.reply("✅ Доступ к Яндекс.Диску имеется")
        await asyncio.sleep(5)
        await msg.delete()
    else:
        await message.reply("❌ Нет доступа к Яндекс.Диску")
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        types.KeyboardButton("➕ Добавить уведомление"),
        types.KeyboardButton("📋 Список уведомлений")
    )
    keyboard.add(
        types.KeyboardButton("⚙️ Настройки"),
        types.KeyboardButton("💾 Создать бэкап")
    )
    
    await message.reply(
        "🤖 **Бот уведомлений запущен!**\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


# Состояния
class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_snooze_hours = State()


class SettingsStates(StatesGroup):
    waiting_for_backup_path = State()
    waiting_for_max_backups = State()
    waiting_for_check_time = State()


# Добавление уведомления
@dp.message_handler(lambda m: m.text == "➕ Добавить уведомление")
async def add_notification_start(message: types.Message, state: FSMContext):
    await message.reply("✏️ **Введите текст уведомления:**", parse_mode='Markdown')
    await state.set_state(NotificationStates.waiting_for_text.state)


@dp.message_handler(state=NotificationStates.waiting_for_text)
async def get_notification_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("⏰ В часах", callback_data="hours"),
        InlineKeyboardButton("📅 В днях", callback_data="days"),
        InlineKeyboardButton("📆 В месяцах", callback_data="months"),
        InlineKeyboardButton("🗓️ Конкретная дата", callback_data="specific")
    )
    
    await message.reply("⏱️ **Через сколько уведомить?**", reply_markup=keyboard, parse_mode='Markdown')
    await state.set_state("waiting_for_time_type")


@dp.callback_query_handler(lambda c: c.data in ['hours', 'days', 'months', 'specific'], state="waiting_for_time_type")
async def get_time_type(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(time_type=callback.data)
    
    if callback.data == 'hours':
        await bot.send_message(callback.from_user.id, "⌛ **Введите количество часов:**", parse_mode='Markdown')
        await state.set_state(NotificationStates.waiting_for_hours.state)
    elif callback.data == 'days':
        await bot.send_message(callback.from_user.id, "📅 **Введите количество дней:**", parse_mode='Markdown')
        await state.set_state(NotificationStates.waiting_for_days.state)
    elif callback.data == 'months':
        await bot.send_message(callback.from_user.id, "📆 **Введите количество месяцев:**", parse_mode='Markdown')
        await state.set_state(NotificationStates.waiting_for_months.state)
    elif callback.data == 'specific':
        await bot.send_message(callback.from_user.id, "🗓️ **Введите дату** (ГГГГ-ММ-ДД ЧЧ:ММ):\nПример: `2024-12-31 23:59`", parse_mode='Markdown')
        await state.set_state(NotificationStates.waiting_for_specific_date.state)
    
    await callback.answer()


@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def set_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        notify_time = datetime.now() + timedelta(hours=hours)
        
        data = await state.get_data()
        notif_id = str(int(datetime.now().timestamp()))
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': datetime.now().isoformat(),
            'notified': False
        }
        
        save_data()
        
        await message.reply(f"✅ **Уведомление создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}", parse_mode='Markdown')
        
        # Создаем бэкап
        success, _ = await create_backup()
        if success:
            msg = await message.reply("✅ Бэкап создан на Яндекс.Диске")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("❌ Не удалось создать бэкап на Яндекс.Диск!\nПроверьте настройки доступа.")
        
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число часов.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_days)
async def set_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        notify_time = datetime.now() + timedelta(days=days)
        
        data = await state.get_data()
        notif_id = str(int(datetime.now().timestamp()))
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': datetime.now().isoformat(),
            'notified': False
        }
        
        save_data()
        
        await message.reply(f"✅ **Уведомление создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}", parse_mode='Markdown')
        
        success, _ = await create_backup()
        if success:
            msg = await message.reply("✅ Бэкап создан на Яндекс.Диске")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("❌ Не удалось создать бэкап на Яндекс.Диск!")
        
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число дней.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_months)
async def set_months(message: types.Message, state: FSMContext):
    try:
        months = int(message.text)
        days = months * 30
        notify_time = datetime.now() + timedelta(days=days)
        
        data = await state.get_data()
        notif_id = str(int(datetime.now().timestamp()))
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': datetime.now().isoformat(),
            'notified': False
        }
        
        save_data()
        
        await message.reply(f"✅ **Уведомление создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}", parse_mode='Markdown')
        
        success, _ = await create_backup()
        if success:
            msg = await message.reply("✅ Бэкап создан на Яндекс.Диске")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("❌ Не удалось создать бэкап на Яндекс.Диск!")
        
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Введите корректное число месяцев.", parse_mode='Markdown')


@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def set_specific_date(message: types.Message, state: FSMContext):
    try:
        notify_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        
        if notify_time < datetime.now():
            await message.reply("❌ **Ошибка!** Дата должна быть в будущем!", parse_mode='Markdown')
            return
        
        data = await state.get_data()
        notif_id = str(int(datetime.now().timestamp()))
        
        notifications[notif_id] = {
            'text': data['text'],
            'time': notify_time.isoformat(),
            'created': datetime.now().isoformat(),
            'notified': False
        }
        
        save_data()
        
        await message.reply(f"✅ **Уведомление создано!**\n📝 {data['text']}\n⏰ {notify_time.strftime('%Y-%m-%d %H:%M:%S')}", parse_mode='Markdown')
        
        success, _ = await create_backup()
        if success:
            msg = await message.reply("✅ Бэкап создан на Яндекс.Диске")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("❌ Не удалось создать бэкап на Яндекс.Диск!")
        
        await state.finish()
    except ValueError:
        await message.reply("❌ **Ошибка!** Неверный формат даты!\nПример: `2024-12-31 23:59`", parse_mode='Markdown')


# Список уведомлений
@dp.message_handler(lambda m: m.text == "📋 Список уведомлений")
async def list_notifications(message: types.Message):
    if not notifications:
        await message.reply("📭 **У вас нет активных уведомлений**", parse_mode='Markdown')
        return
    
    text = "📋 **ВАШИ УВЕДОМЛЕНИЯ:**\n\n"
    for notif_id, notif in list(notifications.items())[:20]:  # Показываем первые 20
        notify_time = datetime.fromisoformat(notif['time'])
        status = "✅" if notif.get('notified') else "⏳"
        text += f"{status} `{notify_time.strftime('%d.%m.%Y %H:%M')}`\n"
        text += f"📝 {notif['text'][:50]}\n"
        text += f"🆔 ID: `{notif_id}`\n\n"
    
    text += "\n💡 **Чтобы удалить уведомление:**\nОтправьте команду:\n`/delete_уведомления_ID`"
    
    await message.reply(text, parse_mode='Markdown')


# Удаление уведомления
@dp.message_handler(lambda m: m.text.startswith('/delete_'))
async def delete_notification(message: types.Message):
    notif_id = message.text.replace('/delete_', '')
    
    if notif_id in notifications:
        del notifications[notif_id]
        save_data()
        
        success, _ = await create_backup()
        if success:
            msg = await message.reply("✅ Уведомление удалено, бэкап создан")
            await asyncio.sleep(60)
            await msg.delete()
        else:
            await message.reply("❌ Уведомление удалено, но не удалось создать бэкап!")
        
        await message.reply("✅ **Уведомление удалено!**", parse_mode='Markdown')
    else:
        await message.reply("❌ **Уведомление не найдено!**", parse_mode='Markdown')


# Обработка кнопок уведомлений
@dp.callback_query_handler(lambda c: c.data.startswith('delete_'))
async def handle_delete_notification(callback: types.CallbackQuery):
    notif_id = callback.data.replace('delete_', '')
    
    if notif_id in notifications:
        del notifications[notif_id]
        save_data()
        
        success, _ = await create_backup()
        if success:
            msg = await bot.send_message(callback.from_user.id, "✅ Бэкап создан")
            await asyncio.sleep(60)
            await msg.delete()
        
        await bot.edit_message_text(
            "✅ **Уведомление удалено**",
            callback.from_user.id,
            callback.message.message_id,
            parse_mode='Markdown'
        )
    else:
        await callback.answer("Уведомление уже удалено")
    
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith('snooze_'))
async def handle_snooze(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    notif_id = parts[1]
    hours = int(parts[2])
    
    if notif_id in notifications:
        new_time = datetime.now() + timedelta(hours=hours)
        notifications[notif_id]['time'] = new_time.isoformat()
        notifications[notif_id]['notified'] = False
        save_data()
        
        await bot.edit_message_text(
            f"⏰ **Уведомление отложено на {hours} час(ов)**\nНовое время: {new_time.strftime('%H:%M %d.%m.%Y')}",
            callback.from_user.id,
            callback.message.message_id,
            parse_mode='Markdown'
        )
    
    await callback.answer()


# Настройки
@dp.message_handler(lambda m: m.text == "⚙️ Настройки")
async def settings_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("📁 Путь на Яндекс.Диске", callback_data="set_backup_path"),
        InlineKeyboardButton("🔢 Максимум бэкапов", callback_data="set_max_backups"),
        InlineKeyboardButton("🕐 Время проверки", callback_data="set_check_time"),
        InlineKeyboardButton("ℹ️ Информация", callback_data="info")
    )
    
    await message.reply("⚙️ **НАСТРОЙКИ**", reply_markup=keyboard, parse_mode='Markdown')


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
    
    # Создаем папку на Яндекс.Диске
    if yandex_disk:
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
        # Проверяем формат времени
        datetime.strptime(message.text, "%H:%M")
        config['daily_check_time'] = message.text
        save_data()
        await message.reply(f"✅ **Время проверки установлено:** `{message.text}`", parse_mode='Markdown')
    except ValueError:
        await message.reply("❌ **Ошибка!** Неверный формат времени. Используйте `ЧЧ:ММ`", parse_mode='Markdown')
    
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == "info")
async def show_info(callback: types.CallbackQuery):
    info = f"""
📊 **СТАТИСТИКА БОТА**

📝 **Активных уведомлений:** `{len(notifications)}`
💾 **Максимум бэкапов:** `{config.get('max_backups', 5)}`
📁 **Путь бэкапов:** `{config['backup_path']}`
🕐 **Проверка в:** `{config.get('daily_check_time', '06:00')}`

📦 **Локальных бэкапов:** `{len(list(Path(BACKUP_DIR).glob('backup_*.json')))}`

🤖 **Статус Яндекс.Диска:** `{'✅ Доступен' if yandex_disk and yandex_disk.check_access() else '❌ Недоступен'}`
"""
    await bot.send_message(callback.from_user.id, info, parse_mode='Markdown')
    await callback.answer()


# Создание бэкапа вручную
@dp.message_handler(lambda m: m.text == "💾 Создать бэкап")
async def manual_backup(message: types.Message):
    await message.reply("⏳ **Создание бэкапа...**", parse_mode='Markdown')
    success, backup_file = await create_backup()
    if success:
        msg = await message.reply("✅ **Бэкап создан на Яндекс.Диске**")
        await asyncio.sleep(60)
        await msg.delete()
    else:
        await message.reply("❌ **Не удалось создать бэкап!**\nПроверьте доступ к Яндекс.Диску", parse_mode='Markdown')


# Обновление базы из бэкапа
@dp.callback_query_handler(lambda c: c.data == "update_database")
async def update_database(callback: types.CallbackQuery):
    try:
        if yandex_disk and yandex_disk.check_access():
            # Получаем последний бэкап с Яндекс.Диска
            files = yandex_disk.list_files(config['backup_path'])
            backup_files = [f for f in files if f['name'].startswith('backup_')]
            
            if backup_files:
                latest_backup = max(backup_files, key=lambda x: x['name'])
                local_backup = Path(BACKUP_DIR) / latest_backup['name']
                
                if yandex_disk.download_file(f"{config['backup_path']}/{latest_backup['name']}", str(local_backup)):
                    with open(local_backup, 'r', encoding='utf-8') as f:
                        backup_data = json.load(f)
                    
                    global notifications
                    notifications = backup_data.get('notifications', {})
                    save_data()
                    
                    await bot.send_message(
                        callback.from_user.id,
                        f"✅ **База обновлена из бэкапа** `{latest_backup['name']}`",
                        parse_mode='Markdown'
                    )
                else:
                    await bot.send_message(callback.from_user.id, "❌ **Ошибка скачивания бэкапа**", parse_mode='Markdown')
            else:
                await bot.send_message(callback.from_user.id, "❌ **Бэкапов не найдено**", parse_mode='Markdown')
        else:
            await bot.send_message(callback.from_user.id, "❌ **Нет доступа к Яндекс.Диску**", parse_mode='Markdown')
    except Exception as e:
        await bot.send_message(callback.from_user.id, f"❌ **Ошибка обновления:** `{e}`", parse_mode='Markdown')
    
    await callback.answer()


# Команда /restart
@dp.message_handler(commands=['restart'])
async def restart_bot(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.reply("🔄 **Перезапуск бота...**", parse_mode='Markdown')
        await asyncio.sleep(2)
        # Перезапуск бота
        os._exit(0)


# Запуск бота
async def on_startup(dp):
    init_folders()
    load_data()
    
    # Проверяем доступ к Яндекс.Диску при старте
    if await check_yandex_access():
        print("✅ Доступ к Яндекс.Диску получен")
    else:
        print("❌ Нет доступа к Яндекс.Диску")
    
    asyncio.create_task(check_notifications())
    asyncio.create_task(daily_check())
    print("✅ Бот успешно запущен!")
    print(f"📝 Загружено уведомлений: {len(notifications)}")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)