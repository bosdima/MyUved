import logging
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import aiohttp
from aiohttp import ClientTimeout

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "YOUR_BOT_TOKEN"  # Замените на ваш токен
YANDEX_CLIENT_ID = "fc732f45864547329b781636c61c64ec"
TIMEZONE = pytz.timezone('Europe/Moscow')

# ==================== СОСТОЯНИЯ FSM ====================
class AuthStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_token = State()

class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_datetime = State()
    waiting_for_repeat = State()

class EditStates(StatesGroup):
    waiting_for_notification_id = State()
    waiting_for_new_text = State()
    waiting_for_new_time = State()

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
user_data = {}  # {user_id: {"notifications": [], "yandex_token": str, "notifications_enabled": bool}}
editing_sessions = {}  # {user_id: {"notification_id": int, "action": str}}
temp_auth = {}  # {user_id: {"method": str, "code": str, "timestamp": datetime}}

# ==================== ФУНКЦИИ РАБОТЫ С ЯНДЕКС.ДИСКОМ ====================
async def check_yandex_access(token: str) -> bool:
    """Проверка доступа к Яндекс.Диску"""
    try:
        async with aiohttp.ClientSession() as session:
            # Проверяем создание папки
            url = "https://cloud-api.yandex.net/v1/disk/resources"
            headers = {"Authorization": f"OAuth {token}"}
            params = {"path": "/MyUved_backups"}
            
            async with session.put(url, headers=headers, params=params) as resp:
                if resp.status not in [200, 201, 409]:
                    return False
            
            # Проверяем права на запись (создаем тестовый файл)
            test_file_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
            test_params = {"path": "/MyUved_backups/test.txt", "overwrite": "true"}
            
            async with session.get(test_file_url, headers=headers, params=test_params) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                upload_url = data.get("href")
            
            if upload_url:
                async with session.put(upload_url, data=b"test") as resp:
                    if resp.status not in [200, 201]:
                        return False
            
            # Удаляем тестовый файл
            delete_params = {"path": "/MyUved_backups/test.txt", "permanently": "true"}
            async with session.delete(url, headers=headers, params=delete_params) as resp:
                pass
            
            return True
    except Exception as e:
        logging.error(f"Ошибка проверки доступа к Яндекс.Диску: {e}")
        return False

async def upload_backup_to_yandex(user_id: int, token: str, data: dict) -> bool:
    """Загрузка бэкапа на Яндекс.Диск"""
    try:
        timestamp = datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{timestamp}.json"
        
        async with aiohttp.ClientSession() as session:
            # Получаем URL для загрузки
            url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
            headers = {"Authorization": f"OAuth {token}"}
            params = {"path": f"/MyUved_backups/{filename}", "overwrite": "true"}
            
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return False
                upload_data = await resp.json()
                upload_url = upload_data.get("href")
            
            if not upload_url:
                return False
            
            # Загружаем файл
            json_data = json.dumps(data, ensure_ascii=False, default=str)
            async with session.put(upload_url, data=json_data.encode('utf-8')) as resp:
                return resp.status in [200, 201]
    except Exception as e:
        logging.error(f"Ошибка загрузки бэкапа: {e}")
        return False

async def download_backup_from_yandex(token: str, filename: str) -> Optional[dict]:
    """Скачивание бэкапа с Яндекс.Диска"""
    try:
        async with aiohttp.ClientSession() as session:
            # Получаем URL для скачивания
            url = "https://cloud-api.yandex.net/v1/disk/resources/download"
            headers = {"Authorization": f"OAuth {token}"}
            params = {"path": f"/MyUved_backups/{filename}"}
            
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return None
                download_data = await resp.json()
                download_url = download_data.get("href")
            
            if not download_url:
                return None
            
            # Скачиваем файл
            async with session.get(download_url) as resp:
                if resp.status != 200:
                    return None
                content = await resp.text()
                return json.loads(content)
    except Exception as e:
        logging.error(f"Ошибка скачивания бэкапа: {e}")
        return None

async def list_backups(token: str) -> List[str]:
    """Получение списка бэкапов"""
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://cloud-api.yandex.net/v1/disk/resources"
            headers = {"Authorization": f"OAuth {token}"}
            params = {"path": "/MyUved_backups", "limit": 100}
            
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                items = data.get("_embedded", {}).get("items", [])
                backups = [item["name"] for item in items if item["name"].startswith("backup_")]
                return sorted(backups, reverse=True)
    except Exception as e:
        logging.error(f"Ошибка получения списка бэкапов: {e}")
        return []

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def format_notification(notif: dict, index: int) -> str:
    """Форматирование уведомления для вывода"""
    status = "✅ АКТИВНО" if notif.get("active", True) else "⏸ ПРИОСТАНОВЛЕНО"
    
    if notif.get("repeat_type") == "once":
        time_str = f"⏰ **Время:** {notif['datetime'].strftime('%d.%m.%Y в %H:%M')}"
        next_time = notif['datetime']
        if next_time > datetime.now(TIMEZONE):
            remaining = next_time - datetime.now(TIMEZONE)
            days = remaining.days
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            remaining_str = f"⏳ Осталось: {days} дн. {hours} ч." if days > 0 else f"⏳ Осталось: {hours} ч. {minutes} мин."
        else:
            remaining_str = "⚠️ ПРОСРОЧЕНО"
    else:
        time_str = f"🔄 **Повтор:** {notif.get('repeat_desc', '')}"
        next_time = notif.get('next_datetime')
        if next_time:
            remaining = next_time - datetime.now(TIMEZONE)
            days = remaining.days
            hours = remaining.seconds // 3600
            remaining_str = f"⏰ **Следующее:** {next_time.strftime('%d.%m.%Y в %H:%M')} (через {days} дн. {hours} ч.)" if days > 0 else f"⏰ **Следующее:** {next_time.strftime('%d.%m.%Y в %H:%M')} (через {hours} ч.)"
        else:
            remaining_str = ""
    
    return f"""{'🔄' if notif.get('repeat_type') != 'once' else '⏳'} **Уведомление #{index}**
📝 **Текст:** {notif['text']}
{time_str}
📊 **Статус:** {status}
{remaining_str}"""

def create_main_keyboard() -> ReplyKeyboardMarkup:
    """Создание главной клавиатуры"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("➕ Добавить уведомление"))
    keyboard.add(KeyboardButton("📋 Список уведомлений"))
    keyboard.add(KeyboardButton("⚙️ Настройки"))
    return keyboard

def calculate_next_datetime(notif: dict) -> Optional[datetime]:
    """Расчет следующей даты для повторяющихся уведомлений"""
    now = datetime.now(TIMEZONE)
    repeat_type = notif.get("repeat_type")
    
    if repeat_type == "once":
        return notif.get("datetime")
    
    elif repeat_type == "daily":
        next_time = now.replace(hour=notif["time"].hour, minute=notif["time"].minute, second=0, microsecond=0)
        if next_time <= now:
            next_time += timedelta(days=1)
        return next_time
    
    elif repeat_type == "weekly":
        next_time = now.replace(hour=notif["time"].hour, minute=notif["time"].minute, second=0, microsecond=0)
        target_weekday = notif["weekday"]
        days_ahead = target_weekday - next_time.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_time += timedelta(days=days_ahead)
        return next_time
    
    elif repeat_type == "monthly":
        next_time = now.replace(day=notif["day"], hour=notif["time"].hour, minute=notif["time"].minute, second=0, microsecond=0)
        if next_time <= now:
            if next_time.month == 12:
                next_time = next_time.replace(year=next_time.year + 1, month=1)
            else:
                next_time = next_time.replace(month=next_time.month + 1)
        return next_time
    
    elif repeat_type == "weekdays":
        next_time = now.replace(hour=notif["time"].hour, minute=notif["time"].minute, second=0, microsecond=0)
        if next_time.weekday() >= 5:  # weekend
            days_ahead = 7 - next_time.weekday()
            next_time += timedelta(days=days_ahead)
        elif next_time <= now:
            next_time += timedelta(days=1)
            if next_time.weekday() >= 5:
                days_ahead = 7 - next_time.weekday()
                next_time += timedelta(days=days_ahead)
        return next_time
    
    return None

def check_notifications(user_id: int) -> List[dict]:
    """Проверка и отправка уведомлений"""
    if user_id not in user_data:
        return []
    
    notifications = user_data[user_id].get("notifications", [])
    now = datetime.now(TIMEZONE)
    triggered = []
    
    for notif in notifications:
        if not notif.get("active", True):
            continue
        
        next_time = notif.get("next_datetime")
        if not next_time:
            next_time = calculate_next_datetime(notif)
            notif["next_datetime"] = next_time
        
        if next_time and next_time <= now:
            triggered.append(notif)
            
            # Обновляем следующее время
            if notif.get("repeat_type") == "once":
                notif["active"] = False
            else:
                new_next = calculate_next_datetime(notif)
                notif["next_datetime"] = new_next
    
    return triggered

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start_command(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    
    if user_id not in user_data:
        user_data[user_id] = {
            "notifications": [],
            "yandex_token": None,
            "notifications_enabled": True
        }
    
    logging.info(f"Пользователь {user_id} запустил бота")
    
    if not user_data[user_id].get("yandex_token"):
        # Нет токена - предлагаем авторизацию
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔑 Авторизация", callback_data="start_auth"))
        
        await message.reply(
            f"👋 **Добро пожаловать!**\n\n"
            f"🤖 **Версия бота:** v2.9 (08.04.2026 11:30)\n\n"
            f"⚠️ **Нет доступа к Яндекс.Диску!**\n\n"
            f"Для работы бэкапов необходимо авторизоваться.\n\n"
            f"Нажмите кнопку ниже для авторизации:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        # Есть токен - показываем главное меню
        await message.reply(
            "👋 **Выберите действие:**",
            parse_mode="Markdown",
            reply_markup=create_main_keyboard()
        )

async def auth_start(callback_query: types.CallbackQuery):
    """Начало авторизации"""
    user_id = callback_query.from_user.id
    logging.info(f"Пользователь {user_id} начал авторизацию")
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔑 Через код авторизации", callback_data="auth_method_code"),
        InlineKeyboardButton("🔓 Через токен напрямую", callback_data="auth_method_token")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_auth"))
    
    await callback_query.message.edit_text(
        "🔐 **Выберите способ авторизации:**\n\n"
        "• **Через код** - стандартный способ, нужно получить код на Яндексе\n"
        "• **Через токен** - отладочный способ, токен можно получить по ссылке",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

async def auth_method_code(callback_query: types.CallbackQuery):
    """Авторизация через код"""
    user_id = callback_query.from_user.id
    
    temp_auth[user_id] = {
        "method": "code",
        "timestamp": datetime.now(TIMEZONE)
    }
    
    await AuthStates.waiting_for_code.set()
    
    await callback_query.message.edit_text(
        "🔑 **Авторизация через код**\n\n"
        "1️⃣ Перейдите по ссылке для авторизации:\n"
        f"🔗 [Нажмите для авторизации](https://oauth.yandex.ru/authorize?response_type=code&client_id={YANDEX_CLIENT_ID}&redirect_uri=https%3A%2F%2Foauth.yandex.ru%2Fverification_code)\n\n"
        "2️⃣ Войдите в аккаунт Яндекс\n"
        "3️⃣ Разрешите доступ\n"
        "4️⃣ Скопируйте код из адресной строки (часть после `code=`)\n"
        "5️⃣ **Отправьте код сюда текстовым сообщением**\n\n"
        "⏰ **У вас есть 3 минуты** на ввод кода\n\n"
        "📝 Пример кода: `5j4iyexor5ltn4ym`",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback_query.answer()

async def auth_method_token(callback_query: types.CallbackQuery):
    """Авторизация через токен"""
    user_id = callback_query.from_user.id
    
    temp_auth[user_id] = {
        "method": "token",
        "timestamp": datetime.now(TIMEZONE)
    }
    
    await AuthStates.waiting_for_token.set()
    
    await callback_query.message.edit_text(
        "🔓 **Авторизация через токен**\n\n"
        "1️⃣ Получите токен по ссылке:\n"
        "🔗 [Получить токен](https://oauth.yandex.ru/authorize?response_type=token&client_id=fc732f45864547329b781636c61c64ec)\n\n"
        "2️⃣ Скопируйте полученный токен\n"
        "3️⃣ **Отправьте токен сюда текстовым сообщением**\n\n"
        "⏰ **У вас есть 3 минуты** на ввод токена\n\n"
        "📝 Пример токена: `y0_AgAAAAA...`",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    await callback_query.answer()

async def process_auth_code(message: types.Message, state: FSMContext):
    """Обработка кода авторизации"""
    user_id = message.from_user.id
    code = message.text.strip()
    
    if user_id not in temp_auth or temp_auth[user_id]["method"] != "code":
        await state.finish()
        await message.reply("❌ Сессия авторизации истекла. Начните заново /start")
        return
    
    # Проверяем время (3 минуты)
    time_diff = (datetime.now(TIMEZONE) - temp_auth[user_id]["timestamp"]).total_seconds()
    if time_diff > 180:
        await state.finish()
        await message.reply("❌ Время истекло. Начните авторизацию заново /start")
        return
    
    status_msg = await message.reply("⏳ **Получение токена...**", parse_mode="Markdown")
    
    try:
        # Обмениваем код на токен
        async with aiohttp.ClientSession() as session:
            url = "https://oauth.yandex.ru/token"
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": YANDEX_CLIENT_ID
            }
            
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    await status_msg.edit_text("❌ **Ошибка авторизации!** Неверный код или код истек.", parse_mode="Markdown")
                    await state.finish()
                    return
                
                result = await resp.json()
                token = result.get("access_token")
                
                if not token:
                    await status_msg.edit_text("❌ **Ошибка!** Токен не получен.", parse_mode="Markdown")
                    await state.finish()
                    return
        
        await status_msg.edit_text("⏳ **Проверка доступа...**", parse_mode="Markdown")
        
        # Проверяем доступ
        if await check_yandex_access(token):
            user_data[user_id]["yandex_token"] = token
            logging.info(f"✅ Доступ к Яндекс.Диску для user {user_id} успешно получен")
            
            await status_msg.delete()
            
            # Проверяем наличие бэкапов
            backups = await list_backups(token)
            
            if backups:
                keyboard = InlineKeyboardMarkup(row_width=2)
                keyboard.add(
                    InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"),
                    InlineKeyboardButton("❌ Нет, спасибо", callback_data="decline_restore")
                )
                
                await message.reply(
                    f"✅ **Авторизация успешна!**\n\n"
                    f"📦 **Найдено бэкапов на Яндекс.Диске:** {len(backups)}\n"
                    f"Хотите восстановить данные из бэкапа?",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            else:
                await message.reply(
                    "✅ **Авторизация успешна!**\n\n"
                    "Теперь вы можете создавать уведомления.",
                    parse_mode="Markdown",
                    reply_markup=create_main_keyboard()
                )
        else:
            await status_msg.edit_text(
                "❌ **Ошибка доступа!**\n\n"
                "Не удалось получить доступ к Яндекс.Диску. Проверьте правильность кода.",
                parse_mode="Markdown"
            )
        
        await state.finish()
        
    except Exception as e:
        logging.error(f"Ошибка авторизации: {e}")
        await status_msg.edit_text("❌ **Ошибка авторизации!** Попробуйте позже.", parse_mode="Markdown")
        await state.finish()

async def process_token(message: types.Message, state: FSMContext):
    """Обработка прямого токена"""
    user_id = message.from_user.id
    token = message.text.strip()
    
    if user_id not in temp_auth or temp_auth[user_id]["method"] != "token":
        await state.finish()
        await message.reply("❌ Сессия авторизации истекла. Начните заново /start")
        return
    
    # Проверяем время (3 минуты)
    time_diff = (datetime.now(TIMEZONE) - temp_auth[user_id]["timestamp"]).total_seconds()
    if time_diff > 180:
        await state.finish()
        await message.reply("❌ Время истекло. Начните авторизацию заново /start")
        return
    
    status_msg = await message.reply("⏳ **Проверка токена...**", parse_mode="Markdown")
    
    # Проверяем доступ
    if await check_yandex_access(token):
        user_data[user_id]["yandex_token"] = token
        logging.info(f"✅ Доступ к Яндекс.Диску для user {user_id} успешно получен")
        
        await status_msg.delete()
        
        # Проверяем наличие бэкапов
        backups = await list_backups(token)
        
        if backups:
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✅ Да, восстановить", callback_data="offer_restore"),
                InlineKeyboardButton("❌ Нет, спасибо", callback_data="decline_restore")
            )
            
            await message.reply(
                f"✅ **Авторизация успешна!**\n\n"
                f"📦 **Найдено бэкапов на Яндекс.Диске:** {len(backups)}\n"
                f"Хотите восстановить данные из бэкапа?",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await message.reply(
                "✅ **Авторизация успешна!**\n\n"
                "Теперь вы можете создавать уведомления.",
                parse_mode="Markdown",
                reply_markup=create_main_keyboard()
            )
    else:
        await status_msg.edit_text(
            "❌ **Ошибка доступа!**\n\n"
            "Не удалось получить доступ к Яндекс.Диску. Проверьте правильность токена.",
            parse_mode="Markdown"
        )
    
    await state.finish()

async def offer_restore(callback_query: types.CallbackQuery):
    """Предложение восстановить бэкап"""
    user_id = callback_query.from_user.id
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    backups = await list_backups(token)
    
    if not backups:
        await callback_query.answer("❌ Бэкапы не найдены", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for backup in backups[:10]:  # Показываем не более 10 бэкапов
        # Извлекаем дату из имени файла
        date_str = backup.replace("backup_", "").replace(".json", "")
        try:
            backup_date = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
            display_name = backup_date.strftime("%d.%m.%Y %H:%M:%S")
        except:
            display_name = date_str
        
        keyboard.add(InlineKeyboardButton(f"📦 {display_name}", callback_data=f"restore_backup_{backup}"))
    
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="decline_restore"))
    
    await callback_query.message.edit_text(
        "📦 **Выберите бэкап для восстановления:**",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

async def restore_backup(callback_query: types.CallbackQuery):
    """Восстановление бэкапа"""
    user_id = callback_query.from_user.id
    backup_filename = callback_query.data.replace("restore_backup_", "")
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    status_msg = await callback_query.message.reply("⏳ **Восстановление из бэкапа...**", parse_mode="Markdown")
    
    backup_data = await download_backup_from_yandex(token, backup_filename)
    
    if backup_data:
        user_data[user_id]["notifications"] = backup_data.get("notifications", [])
        notifications_count = len(user_data[user_id]["notifications"])
        
        await status_msg.edit_text(
            f"✅ **Данные успешно восстановлены из бэкапа!**\n\n"
            f"📝 Уведомлений: {notifications_count}",
            parse_mode="Markdown"
        )
        
        # Показываем главное меню
        await callback_query.message.answer(
            "👋 **Выберите действие:**",
            parse_mode="Markdown",
            reply_markup=create_main_keyboard()
        )
    else:
        await status_msg.edit_text("❌ **Ошибка восстановления!** Бэкап поврежден или не найден.", parse_mode="Markdown")
    
    await callback_query.answer()

async def decline_restore(callback_query: types.CallbackQuery):
    """Отказ от восстановления"""
    user_id = callback_query.from_user.id
    
    await callback_query.message.edit_text(
        "✅ **Авторизация успешна!**\n\n"
        "Теперь вы можете создавать уведомления.",
        parse_mode="Markdown"
    )
    await callback_query.message.answer(
        "👋 **Выберите действие:**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )
    await callback_query.answer()

async def cancel_auth(callback_query: types.CallbackQuery):
    """Отмена авторизации"""
    user_id = callback_query.from_user.id
    
    await callback_query.message.edit_text("❌ **Авторизация отменена**")
    await callback_query.message.answer(
        "👋 **Выберите действие:**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )
    await callback_query.answer()

async def show_notifications(message: types.Message):
    """Показать список уведомлений"""
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил список уведомлений")
    
    if user_id not in user_data:
        user_data[user_id] = {
            "notifications": [],
            "yandex_token": None,
            "notifications_enabled": True
        }
    
    notifications = user_data[user_id].get("notifications", [])
    
    if not notifications:
        await message.reply(
            "📋 **Список уведомлений пуст.**\n\n"
            "Нажмите «➕ Добавить уведомление», чтобы создать новое.",
            parse_mode="Markdown"
        )
        return
    
    # Обновляем следующее время для всех уведомлений
    for notif in notifications:
        if notif.get("active", True) and not notif.get("next_datetime"):
            notif["next_datetime"] = calculate_next_datetime(notif)
    
    # Отправляем уведомления по одному
    for i, notif in enumerate(notifications, 1):
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_notification_{i-1}"),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_notification_{i-1}")
        )
        
        await message.reply(
            format_notification(notif, i),
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    active_count = sum(1 for n in notifications if n.get("active", True))
    await message.reply(
        f"📊 **Всего уведомлений:** {len(notifications)}\n"
        f"💡 **Активных:** {active_count}",
        parse_mode="Markdown"
    )

async def edit_notification(callback_query: types.CallbackQuery):
    """Начать редактирование уведомления"""
    user_id = callback_query.from_user.id
    # Извлекаем индекс уведомления
    notif_index = int(callback_query.data.split("_")[-1])
    
    notifications = user_data[user_id].get("notifications", [])
    
    if notif_index >= len(notifications):
        await callback_query.answer("❌ Уведомление не найдено", show_alert=True)
        return
    
    notif = notifications[notif_index]
    logging.info(f"Пользователь {user_id} начал редактирование уведомления {notif_index}")
    
    # Сохраняем индекс в сессии
    editing_sessions[user_id] = {
        "notification_id": notif_index,
        "action": None
    }
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit_text_{notif_index}"),
        InlineKeyboardButton("⏰ Изменить время", callback_data=f"edit_time_{notif_index}")
    )
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit"))
    
    await callback_query.message.reply(
        f"✏️ **Что хотите изменить в уведомлении #{notif_index + 1}?**\n\n"
        f"📝 Текст: {notif['text'][:50]}...\n\n"
        f"⏰ **У вас есть 3 минуты** на выбор действия",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

async def edit_text_action(callback_query: types.CallbackQuery):
    """Изменить текст уведомления"""
    user_id = callback_query.from_user.id
    # Извлекаем индекс из callback_data
    notif_index = int(callback_query.data.split("_")[-1])
    
    if user_id not in editing_sessions or editing_sessions[user_id]["notification_id"] != notif_index:
        editing_sessions[user_id] = {
            "notification_id": notif_index,
            "action": "text"
        }
    else:
        editing_sessions[user_id]["action"] = "text"
    
    await EditStates.waiting_for_new_text.set()
    
    await callback_query.message.edit_text(
        f"✏️ **Введите новый текст для уведомления #{notif_index + 1}**\n\n"
        f"📝 **Текст должен быть не более 200 символов**\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод текста\n\n"
        f"❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )
    await callback_query.answer()

async def edit_time_action(callback_query: types.CallbackQuery):
    """Изменить время уведомления"""
    user_id = callback_query.from_user.id
    # Извлекаем индекс из callback_data
    notif_index = int(callback_query.data.split("_")[-1])
    
    if user_id not in editing_sessions or editing_sessions[user_id]["notification_id"] != notif_index:
        editing_sessions[user_id] = {
            "notification_id": notif_index,
            "action": "time"
        }
    else:
        editing_sessions[user_id]["action"] = "time"
    
    await EditStates.waiting_for_new_time.set()
    
    await callback_query.message.edit_text(
        f"⏰ **Введите новое время для уведомления #{notif_index + 1}**\n\n"
        f"📅 **Формат:** ДД.ММ.ГГГГ ЧЧ:ММ\n"
        f"📝 **Пример:** 25.12.2026 15:30\n\n"
        f"🔄 **Для повторяющихся уведомлений:**\n"
        f"• Ежедневно: каждый день в указанное время\n"
        f"• Еженедельно: укажите день недели\n\n"
        f"⏰ **У вас есть 3 минуты** на ввод времени\n\n"
        f"❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )
    await callback_query.answer()

async def process_new_text(message: types.Message, state: FSMContext):
    """Обработка нового текста уведомления"""
    user_id = message.from_user.id
    new_text = message.text.strip()
    
    if user_id not in editing_sessions:
        await state.finish()
        await message.reply("❌ Сессия редактирования истекла. Начните заново.")
        return
    
    notif_index = editing_sessions[user_id]["notification_id"]
    notifications = user_data[user_id].get("notifications", [])
    
    if notif_index >= len(notifications):
        await state.finish()
        await message.reply("❌ Уведомление не найдено")
        return
    
    if len(new_text) > 200:
        await message.reply("❌ **Текст слишком длинный!** Максимум 200 символов. Попробуйте еще раз:")
        return
    
    # Обновляем текст
    notifications[notif_index]["text"] = new_text
    
    # Создаем бэкап
    token = user_data[user_id].get("yandex_token")
    if token:
        backup_data = {
            "notifications": notifications,
            "last_backup": datetime.now(TIMEZONE).isoformat()
        }
        await upload_backup_to_yandex(user_id, token, backup_data)
    
    await state.finish()
    
    await message.reply(
        f"✅ **Текст уведомления #{notif_index + 1} успешно обновлен!**\n\n"
        f"📝 Новый текст: {new_text}",
        parse_mode="Markdown"
    )
    
    # Показываем обновленное уведомление
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_notification_{notif_index}"),
        InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_notification_{notif_index}")
    )
    
    await message.reply(
        format_notification(notifications[notif_index], notif_index + 1),
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def process_new_time(message: types.Message, state: FSMContext):
    """Обработка нового времени уведомления"""
    user_id = message.from_user.id
    time_str = message.text.strip()
    
    if user_id not in editing_sessions:
        await state.finish()
        await message.reply("❌ Сессия редактирования истекла. Начните заново.")
        return
    
    notif_index = editing_sessions[user_id]["notification_id"]
    notifications = user_data[user_id].get("notifications", [])
    
    if notif_index >= len(notifications):
        await state.finish()
        await message.reply("❌ Уведомление не найдено")
        return
    
    # Парсим время
    try:
        # Пробуем распарсить дату и время
        new_datetime = datetime.strptime(time_str, "%d.%m.%Y %H:%M")
        new_datetime = TIMEZONE.localize(new_datetime)
        
        if new_datetime < datetime.now(TIMEZONE):
            await message.reply("❌ **Дата и время должны быть в будущем!** Попробуйте еще раз:")
            return
        
        # Обновляем уведомление
        notifications[notif_index]["datetime"] = new_datetime
        notifications[notif_index]["repeat_type"] = "once"
        notifications[notif_index]["next_datetime"] = new_datetime
        notifications[notif_index]["active"] = True
        
    except ValueError:
        # Пробуем распарсить только время (для повторяющихся)
        try:
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            
            # Определяем тип повтора (можно спросить у пользователя)
            await message.reply(
                f"⏰ **Выберите тип повтора для времени {time_str}:**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(row_width=2).add(
                    InlineKeyboardButton("📅 Один раз", callback_data=f"repeat_once_{notif_index}"),
                    InlineKeyboardButton("📆 Ежедневно", callback_data=f"repeat_daily_{notif_index}"),
                    InlineKeyboardButton("📊 По будням", callback_data=f"repeat_weekdays_{notif_index}"),
                    InlineKeyboardButton("❌ Отмена", callback_data="cancel_edit")
                )
            )
            await state.finish()
            return
            
        except ValueError:
            await message.reply(
                "❌ **Неверный формат!**\n\n"
                "Используйте:\n"
                "• ДД.ММ.ГГГГ ЧЧ:ММ - для однократного\n"
                "• ЧЧ:ММ - для повторяющегося\n\n"
                "Пример: 25.12.2026 15:30 или 15:30",
                parse_mode="Markdown"
            )
            return
    
    # Создаем бэкап
    token = user_data[user_id].get("yandex_token")
    if token:
        backup_data = {
            "notifications": notifications,
            "last_backup": datetime.now(TIMEZONE).isoformat()
        }
        await upload_backup_to_yandex(user_id, token, backup_data)
    
    await state.finish()
    
    await message.reply(
        f"✅ **Время уведомления #{notif_index + 1} успешно обновлено!**",
        parse_mode="Markdown"
    )
    
    # Показываем обновленное уведомление
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_notification_{notif_index}"),
        InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_notification_{notif_index}")
    )
    
    await message.reply(
        format_notification(notifications[notif_index], notif_index + 1),
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def delete_notification(callback_query: types.CallbackQuery):
    """Удаление уведомления"""
    user_id = callback_query.from_user.id
    notif_index = int(callback_query.data.split("_")[-1])
    
    notifications = user_data[user_id].get("notifications", [])
    
    if notif_index >= len(notifications):
        await callback_query.answer("❌ Уведомление не найдено", show_alert=True)
        return
    
    deleted_text = notifications[notif_index]["text"]
    notifications.pop(notif_index)
    
    # Создаем бэкап
    token = user_data[user_id].get("yandex_token")
    if token:
        backup_data = {
            "notifications": notifications,
            "last_backup": datetime.now(TIMEZONE).isoformat()
        }
        await upload_backup_to_yandex(user_id, token, backup_data)
    
    await callback_query.message.edit_text(
        f"✅ **Уведомление удалено!**\n\n"
        f"📝 Текст: {deleted_text}",
        parse_mode="Markdown"
    )
    await callback_query.answer()

async def cancel_edit(callback_query: types.CallbackQuery):
    """Отмена редактирования"""
    user_id = callback_query.from_user.id
    
    if user_id in editing_sessions:
        del editing_sessions[user_id]
    
    await callback_query.message.edit_text("❌ **Редактирование отменено**")
    await callback_query.message.answer(
        "👋 **Выберите действие:**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )
    await callback_query.answer()

async def cancel_command(message: types.Message, state: FSMContext):
    """Отмена текущей операции"""
    await state.finish()
    
    if message.from_user.id in editing_sessions:
        del editing_sessions[message.from_user.id]
    
    await message.reply(
        "❌ **Операция отменена**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )

async def settings_menu(message: types.Message):
    """Меню настроек"""
    user_id = message.from_user.id
    
    if user_id not in user_data:
        user_data[user_id] = {
            "notifications": [],
            "yandex_token": None,
            "notifications_enabled": True
        }
    
    notifications_enabled = user_data[user_id].get("notifications_enabled", True)
    has_token = user_data[user_id].get("yandex_token") is not None
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    # Кнопка включения/выключения уведомлений
    if notifications_enabled:
        keyboard.add(InlineKeyboardButton("🔕 Выключить уведомления", callback_data="toggle_notifications_off"))
    else:
        keyboard.add(InlineKeyboardButton("🔔 Включить уведомления", callback_data="toggle_notifications_on"))
    
    # Кнопка бэкапа
    if has_token:
        keyboard.add(InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup"))
        keyboard.add(InlineKeyboardButton("📥 Восстановить из бэкапа", callback_data="restore_from_backup"))
        keyboard.add(InlineKeyboardButton("🗑️ Очистить бэкапы", callback_data="clear_backups"))
    else:
        keyboard.add(InlineKeyboardButton("🔑 Авторизация Яндекс.Диск", callback_data="start_auth"))
    
    keyboard.add(InlineKeyboardButton("❌ Закрыть", callback_data="close_settings"))
    
    await message.reply(
        "⚙️ **Настройки бота**\n\n"
        f"🔔 Уведомления: {'✅ Включены' if notifications_enabled else '❌ Выключены'}\n"
        f"💾 Яндекс.Диск: {'✅ Подключен' if has_token else '❌ Не подключен'}",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def toggle_notifications(callback_query: types.CallbackQuery):
    """Включение/выключение уведомлений"""
    user_id = callback_query.from_user.id
    action = callback_query.data.split("_")[-1]  # on или off
    
    if action == "on":
        user_data[user_id]["notifications_enabled"] = True
        await callback_query.answer("🔔 Уведомления включены")
    else:
        user_data[user_id]["notifications_enabled"] = False
        await callback_query.answer("🔕 Уведомления выключены")
    
    # Обновляем сообщение
    await settings_menu(callback_query.message)
    await callback_query.message.delete()
    await callback_query.message.answer(
        "👋 **Выберите действие:**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )

async def create_backup(callback_query: types.CallbackQuery):
    """Создание бэкапа"""
    user_id = callback_query.from_user.id
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    notifications = user_data[user_id].get("notifications", [])
    backup_data = {
        "notifications": notifications,
        "last_backup": datetime.now(TIMEZONE).isoformat()
    }
    
    status_msg = await callback_query.message.reply("⏳ **Создание бэкапа...**", parse_mode="Markdown")
    
    if await upload_backup_to_yandex(user_id, token, backup_data):
        await status_msg.edit_text(
            f"✅ **Бэкап успешно создан!**\n\n"
            f"📝 Уведомлений: {len(notifications)}\n"
            f"🕐 Время: {datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M:%S')}",
            parse_mode="Markdown"
        )
    else:
        await status_msg.edit_text("❌ **Ошибка создания бэкапа!**", parse_mode="Markdown")
    
    await callback_query.answer()

async def restore_from_backup(callback_query: types.CallbackQuery):
    """Восстановление из бэкапа"""
    user_id = callback_query.from_user.id
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    backups = await list_backups(token)
    
    if not backups:
        await callback_query.answer("❌ Бэкапы не найдены", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for backup in backups[:10]:
        date_str = backup.replace("backup_", "").replace(".json", "")
        try:
            backup_date = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
            display_name = backup_date.strftime("%d.%m.%Y %H:%M:%S")
        except:
            display_name = date_str
        
        keyboard.add(InlineKeyboardButton(f"📦 {display_name}", callback_data=f"restore_backup_{backup}"))
    
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="close_settings"))
    
    await callback_query.message.edit_text(
        "📦 **Выберите бэкап для восстановления:**",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

async def clear_backups(callback_query: types.CallbackQuery):
    """Очистка бэкапов"""
    user_id = callback_query.from_user.id
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    backups = await list_backups(token)
    
    if not backups:
        await callback_query.answer("❌ Бэкапы не найдены", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Да, очистить все", callback_data="confirm_clear_backups"),
        InlineKeyboardButton("❌ Нет, отмена", callback_data="close_settings")
    )
    
    await callback_query.message.edit_text(
        f"⚠️ **Вы уверены, что хотите удалить все бэкапы?**\n\n"
        f"📦 Найдено бэкапов: {len(backups)}\n\n"
        f"Это действие нельзя отменить!",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    await callback_query.answer()

async def confirm_clear_backups(callback_query: types.CallbackQuery):
    """Подтверждение очистки бэкапов"""
    user_id = callback_query.from_user.id
    token = user_data[user_id].get("yandex_token")
    
    if not token:
        await callback_query.answer("❌ Нет доступа к Яндекс.Диску", show_alert=True)
        return
    
    backups = await list_backups(token)
    deleted_count = 0
    
    async with aiohttp.ClientSession() as session:
        url = "https://cloud-api.yandex.net/v1/disk/resources"
        headers = {"Authorization": f"OAuth {token}"}
        
        for backup in backups:
            params = {"path": f"/MyUved_backups/{backup}", "permanently": "true"}
            async with session.delete(url, headers=headers, params=params) as resp:
                if resp.status in [200, 204]:
                    deleted_count += 1
    
    await callback_query.message.edit_text(
        f"✅ **Очистка завершена!**\n\n"
        f"🗑️ Удалено бэкапов: {deleted_count}",
        parse_mode="Markdown"
    )
    await callback_query.answer()

async def close_settings(callback_query: types.CallbackQuery):
    """Закрыть настройки"""
    await callback_query.message.delete()
    await callback_query.message.answer(
        "👋 **Выберите действие:**",
        parse_mode="Markdown",
        reply_markup=create_main_keyboard()
    )
    await callback_query.answer()

# ==================== ОБРАБОТЧИКИ ДОБАВЛЕНИЯ УВЕДОМЛЕНИЙ ====================
async def add_notification(message: types.Message):
    """Начать добавление уведомления"""
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} начал добавление уведомления")
    
    await NotificationStates.waiting_for_text.set()
    await message.reply(
        "📝 **Введите текст уведомления**\n\n"
        "📝 **Текст должен быть не более 200 символов**\n\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )

async def process_notification_text(message: types.Message, state: FSMContext):
    """Обработка текста уведомления"""
    text = message.text.strip()
    
    if len(text) > 200:
        await message.reply("❌ **Текст слишком длинный!** Максимум 200 символов. Попробуйте еще раз:")
        return
    
    await state.update_data(text=text)
    await NotificationStates.waiting_for_datetime.set()
    
    await message.reply(
        "⏰ **Введите дату и время уведомления**\n\n"
        "📅 **Формат:** ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "📝 **Пример:** 25.12.2026 15:30\n\n"
        "🔄 **Для повторяющихся уведомлений:**\n"
        "• Ежедневно: каждый день в указанное время\n"
        "• Еженедельно: укажите день недели\n\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )

async def process_notification_datetime(message: types.Message, state: FSMContext):
    """Обработка даты и времени уведомления"""
    time_str = message.text.strip()
    
    try:
        # Пробуем распарсить дату и время
        datetime_obj = datetime.strptime(time_str, "%d.%m.%Y %H:%M")
        datetime_obj = TIMEZONE.localize(datetime_obj)
        
        if datetime_obj < datetime.now(TIMEZONE):
            await message.reply("❌ **Дата и время должны быть в будущем!** Попробуйте еще раз:")
            return
        
        data = await state.get_data()
        text = data.get("text")
        
        # Сохраняем уведомление
        user_id = message.from_user.id
        if user_id not in user_data:
            user_data[user_id] = {
                "notifications": [],
                "yandex_token": None,
                "notifications_enabled": True
            }
        
        notification = {
            "text": text,
            "datetime": datetime_obj,
            "repeat_type": "once",
            "active": True,
            "next_datetime": datetime_obj
        }
        
        user_data[user_id]["notifications"].append(notification)
        
        # Создаем бэкап
        token = user_data[user_id].get("yandex_token")
        if token:
            backup_data = {
                "notifications": user_data[user_id]["notifications"],
                "last_backup": datetime.now(TIMEZONE).isoformat()
            }
            await upload_backup_to_yandex(user_id, token, backup_data)
        
        await state.finish()
        
        await message.reply(
            f"✅ **Уведомление успешно создано!**\n\n"
            f"📝 Текст: {text}\n"
            f"⏰ Время: {datetime_obj.strftime('%d.%m.%Y в %H:%M')}",
            parse_mode="Markdown",
            reply_markup=create_main_keyboard()
        )
        
    except ValueError:
        await message.reply(
            "❌ **Неверный формат!**\n\n"
            "Используйте формат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
            "Пример: 25.12.2026 15:30",
            parse_mode="Markdown"
        )

# ==================== ФОН ЗАДАЧИ ====================
async def notification_checker(dp: Dispatcher):
    """Фоновая задача для проверки уведомлений"""
    while True:
        try:
            now = datetime.now(TIMEZONE)
            
            for user_id, data in user_data.items():
                if not data.get("notifications_enabled", True):
                    continue
                
                notifications = data.get("notifications", [])
                triggered = check_notifications(user_id)
                
                for notif in triggered:
                    try:
                        await dp.bot.send_message(
                            user_id,
                            f"🔔 **Уведомление!**\n\n"
                            f"📝 {notif['text']}",
                            parse_mode="Markdown"
                        )
                        logging.info(f"Отправлено уведомление пользователю {user_id}: {notif['text']}")
                    except Exception as e:
                        logging.error(f"Ошибка отправки уведомления {user_id}: {e}")
            
            # Создаем автоматический бэкап раз в день
            if now.hour == 2 and now.minute == 0:  # В 2:00 ночи
                for user_id, data in user_data.items():
                    token = data.get("yandex_token")
                    if token:
                        backup_data = {
                            "notifications": data.get("notifications", []),
                            "last_backup": now.isoformat()
                        }
                        await upload_backup_to_yandex(user_id, token, backup_data)
                        logging.info(f"Автоматический бэкап создан для {user_id}")
            
        except Exception as e:
            logging.error(f"Ошибка в проверке уведомлений: {e}")
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

# ==================== ЗАПУСК БОТА ====================
async def on_startup(dp: Dispatcher):
    """Действия при запуске"""
    logging.info("=" * 50)
    logging.info("🤖 БОТ ДЛЯ УВЕДОМЛЕНИЙ v2.9 (08.04.2026 11:30)")
    logging.info("=" * 50)
    
    # Запускаем фоновую задачу
    asyncio.create_task(notification_checker(dp))
    
    logging.info("✅ Бот успешно запущен!")

def register_handlers(dp: Dispatcher):
    """Регистрация всех обработчиков"""
    # Команды
    dp.register_message_handler(start_command, commands=["start"])
    dp.register_message_handler(cancel_command, commands=["cancel"], state="*")
    
    # Авторизация
    dp.register_callback_query_handler(auth_start, lambda c: c.data == "start_auth")
    dp.register_callback_query_handler(auth_method_code, lambda c: c.data == "auth_method_code")
    dp.register_callback_query_handler(auth_method_token, lambda c: c.data == "auth_method_token")
    dp.register_callback_query_handler(cancel_auth, lambda c: c.data == "cancel_auth")
    dp.register_callback_query_handler(offer_restore, lambda c: c.data == "offer_restore")
    dp.register_callback_query_handler(decline_restore, lambda c: c.data == "decline_restore")
    dp.register_callback_query_handler(restore_backup, lambda c: c.data and c.data.startswith("restore_backup_"))
    
    # Сообщения для авторизации
    dp.register_message_handler(process_auth_code, state=AuthStates.waiting_for_code)
    dp.register_message_handler(process_token, state=AuthStates.waiting_for_token)
    
    # Уведомления
    dp.register_message_handler(show_notifications, lambda m: m.text == "📋 Список уведомлений")
    dp.register_message_handler(add_notification, lambda m: m.text == "➕ Добавить уведомление")
    dp.register_message_handler(settings_menu, lambda m: m.text == "⚙️ Настройки")
    
    # FSM для добавления уведомлений
    dp.register_message_handler(process_notification_text, state=NotificationStates.waiting_for_text)
    dp.register_message_handler(process_notification_datetime, state=NotificationStates.waiting_for_datetime)
    
    # Редактирование уведомлений
    dp.register_callback_query_handler(edit_notification, lambda c: c.data and c.data.startswith("edit_notification_"))
    dp.register_callback_query_handler(edit_text_action, lambda c: c.data and c.data.startswith("edit_text_"))
    dp.register_callback_query_handler(edit_time_action, lambda c: c.data and c.data.startswith("edit_time_"))
    dp.register_callback_query_handler(delete_notification, lambda c: c.data and c.data.startswith("delete_notification_"))
    dp.register_callback_query_handler(cancel_edit, lambda c: c.data == "cancel_edit")
    
    # FSM для редактирования
    dp.register_message_handler(process_new_text, state=EditStates.waiting_for_new_text)
    dp.register_message_handler(process_new_time, state=EditStates.waiting_for_new_time)
    
    # Настройки
    dp.register_callback_query_handler(toggle_notifications, lambda c: c.data and c.data.startswith("toggle_notifications_"))
    dp.register_callback_query_handler(create_backup, lambda c: c.data == "create_backup")
    dp.register_callback_query_handler(restore_from_backup, lambda c: c.data == "restore_from_backup")
    dp.register_callback_query_handler(clear_backups, lambda c: c.data == "clear_backups")
    dp.register_callback_query_handler(confirm_clear_backups, lambda c: c.data == "confirm_clear_backups")
    dp.register_callback_query_handler(close_settings, lambda c: c.data == "close_settings")

def main():
    """Главная функция"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(bot)
    dp.middleware.setup(LoggingMiddleware())
    
    register_handlers(dp)
    
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)

if __name__ == "__main__":
    main()