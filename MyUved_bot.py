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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv
import yadisk

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
YANDEX_TOKEN = os.getenv('YANDEX_TOKEN')
BACKUP_FOLDER = os.getenv('BACKUP_FOLDER')

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Инициализация Яндекс.Диска
yandex_client = yadisk.YaDisk(token=YANDEX_TOKEN)

# Файлы для хранения данных
DATA_FILE = 'notifications.json'
SETTINGS_FILE = 'settings.json'
TEMP_BACKUP_FOLDER = 'temp_backups'

# Состояния для FSM
class NotificationStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_time_type = State()
    waiting_for_hours = State()
    waiting_for_days = State()
    waiting_for_months = State()
    waiting_for_specific_date = State()
    waiting_for_snooze_hours = State()

class SettingsStates(StatesGroup):
    waiting_for_backup_path = State()

# Структура данных
notifications: Dict[int, Dict] = {}
settings: Dict = {
    'backup_path': BACKUP_FOLDER,
    'check_time': '06:00',
    'auto_cleanup_days': 30,
    'reminder_repeat_hours': 1
}

def load_data():
    """Загрузка данных из файлов"""
    global notifications, settings
    
    # Загрузка уведомлений
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                notifications = json.load(f)
                # Конвертируем ключи обратно в int
                notifications = {int(k): v for k, v in notifications.items()}
        except:
            notifications = {}
    
    # Загрузка настроек
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings.update(json.load(f))
        except:
            pass

def save_data():
    """Сохранение данных в файлы"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(notifications, f, ensure_ascii=False, indent=2, default=str)
    
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

async def check_yandex_access() -> bool:
    """Проверка доступа к Яндекс.Диску"""
    try:
        yandex_client.check_token()
        return True
    except:
        return False

async def create_backup(reason: str = "") -> bool:
    """Создание бэкапа на Яндекс.Диск"""
    try:
        # Создаем временную папку для бэкапа
        os.makedirs(TEMP_BACKUP_FOLDER, exist_ok=True)
        
        # Копируем файлы
        backup_files = [DATA_FILE, SETTINGS_FILE]
        for file in backup_files:
            if os.path.exists(file):
                shutil.copy2(file, os.path.join(TEMP_BACKUP_FOLDER, file))
        
        # Создаем архив с timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f'backup_{timestamp}.zip'
        
        # Архивируем
        shutil.make_archive(os.path.join(TEMP_BACKUP_FOLDER, f'backup_{timestamp}'), 'zip', TEMP_BACKUP_FOLDER)
        
        # Загружаем на Яндекс.Диск
        backup_path_on_disk = os.path.join(settings['backup_path'], backup_name)
        yandex_client.upload(os.path.join(TEMP_BACKUP_FOLDER, f'backup_{timestamp}.zip'), backup_path_on_disk)
        
        # Очищаем старые бэкапы (оставляем последние 5)
        await cleanup_old_backups()
        
        # Удаляем временные файлы
        shutil.rmtree(TEMP_BACKUP_FOLDER)
        
        return True
    except Exception as e:
        print(f"Ошибка бэкапа: {e}")
        return False

async def cleanup_old_backups():
    """Очистка старых бэкапов, оставляет только последние 5"""
    try:
        files = yandex_client.listdir(settings['backup_path'])
        backup_files = [f for f in files if f.name.startswith('backup_') and f.name.endswith('.zip')]
        backup_files.sort(key=lambda x: x.name, reverse=True)
        
        # Удаляем файлы, начиная с 6-го
        for old_file in backup_files[5:]:
            yandex_client.remove(os.path.join(settings['backup_path'], old_file.name))
    except:
        pass

async def show_backup_message(message: types.Message, success: bool):
    """Показать сообщение о результате бэкапа на 1 минуту"""
    if success:
        msg = await message.answer("✅ Бэкап успешно создан на Яндекс.Диске!")
    else:
        msg = await message.answer("❌ Не удалось создать бэкап на Яндекс.Диск!")
    
    await asyncio.sleep(60)
    await msg.delete()

async def daily_check():
    """Ежедневная проверка в 6:00"""
    while True:
        now = datetime.now()
        target_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        
        if now >= target_time:
            target_time += timedelta(days=1)
        
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        # Проверка доступа
        access = await check_yandex_access()
        
        if access:
            # Проверяем наличие последних сохранений
            try:
                files = yandex_client.listdir(settings['backup_path'])
                if files:
                    await bot.send_message(ADMIN_ID, 
                        "✅ Доступ к Яндекс.Диску есть!\n"
                        "📦 Обнаружены сохранения.\n"
                        "Желаете обновить базу уведомлений и настройки?",
                        reply_markup=get_update_keyboard())
            except:
                await bot.send_message(ADMIN_ID, "✅ Доступ к Яндекс.Диску есть!")
        else:
            # Отправляем инструкцию
            instruction = """
❌ НЕТ ДОСТУПА К ЯНДЕКС ДИСКУ!

📝 Инструкция по получению YANDEX_TOKEN:

1. Перейдите на https://yandex.ru/dev/disk/rest/
2. Нажмите "Получить токен"
3. Авторизуйтесь под своей учетной записью
4. Нажмите "Разрешить" для доступа к Яндекс.Диску
5. Скопируйте полученный токен
6. Добавьте токен в файл .env: YANDEX_TOKEN=ваш_токен
7. Перезапустите бота

⚠️ Важно: Токен дает полный доступ к вашему Яндекс.Диску!
"""
            await bot.send_message(ADMIN_ID, instruction)

def get_update_keyboard():
    """Клавиатура для обновления базы"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Обновить базу", callback_data="update_database"))
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel_update"))
    return keyboard

def get_notification_keyboard(notification_id: int):
    """Клавиатура для уведомления"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Выполнено (удалить)", callback_data=f"complete_{notification_id}"))
    keyboard.add(InlineKeyboardButton("⏰ Отложить", callback_data=f"snooze_{notification_id}"))
    return keyboard

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет доступа к этому боту!")
        return
    
    # Проверка доступа при запуске
    access = await check_yandex_access()
    
    if access:
        msg = await message.answer("✅ Доступ к Яндекс.Диску имеется!")
        await asyncio.sleep(5)
        await msg.delete()
    else:
        instruction = """
❌ НЕТ ДОСТУПА К ЯНДЕКС ДИСКУ!

📝 Инструкция по получению YANDEX_TOKEN:

1. Перейдите на https://yandex.ru/dev/disk/rest/
2. Нажмите "Получить токен"
3. Авторизуйтесь под своей учетной записью
4. Нажмите "Разрешить" для доступа к Яндекс.Диску
5. Скопируйте полученный токен
6. Добавьте токен в файл .env: YANDEX_TOKEN=ваш_токен
7. Перезапустите бота

⚠️ Важно: Токен дает полный доступ к вашему Яндекс.Диску!
"""
        await message.answer(instruction)
        return
    
    # Основное меню
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📝 Список уведомлений"))
    keyboard.add(KeyboardButton("➕ Добавить уведомление"))
    keyboard.add(KeyboardButton("⚙️ Настройки"))
    
    await message.answer("👋 Добро пожаловать!\nВыберите действие:", reply_markup=keyboard)

@dp.message_handler(lambda message: message.text == "➕ Добавить уведомление")
async def add_notification_start(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите текст уведомления:")
    await NotificationStates.waiting_for_text.set()

@dp.message_handler(state=NotificationStates.waiting_for_text)
async def process_notification_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("⏰ В часах"))
    keyboard.add(KeyboardButton("📅 В днях"))
    keyboard.add(KeyboardButton("📆 В месяцах"))
    keyboard.add(KeyboardButton("🎯 Конкретная дата"))
    keyboard.add(KeyboardButton("❌ Отмена"))
    
    await message.answer("⏰ Выберите тип времени:", reply_markup=keyboard)
    await NotificationStates.waiting_for_time_type.set()

@dp.message_handler(state=NotificationStates.waiting_for_time_type)
async def process_time_type(message: types.Message, state: FSMContext):
    time_type = message.text
    
    if time_type == "❌ Отмена":
        await state.finish()
        await cmd_start(message)
        return
    
    await state.update_data(time_type=time_type)
    
    if time_type == "⏰ В часах":
        await message.answer("🕐 Через сколько часов уведомить? (введите число)")
        await NotificationStates.waiting_for_hours.set()
    elif time_type == "📅 В днях":
        await message.answer("📅 Через сколько дней уведомить? (введите число)")
        await NotificationStates.waiting_for_days.set()
    elif time_type == "📆 В месяцах":
        await message.answer("📆 Через сколько месяцев уведомить? (введите число)")
        await NotificationStates.waiting_for_months.set()
    elif time_type == "🎯 Конкретная дата":
        await message.answer("📅 Введите дату в формате ГГГГ-ММ-ДД ЧЧ:ММ (например: 2024-12-31 23:59)")
        await NotificationStates.waiting_for_specific_date.set()

@dp.message_handler(state=NotificationStates.waiting_for_hours)
async def process_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        trigger_time = datetime.now() + timedelta(hours=hours)
        
        data = await state.get_data()
        notification_id = max(notifications.keys(), default=0) + 1
        
        notifications[notification_id] = {
            'id': notification_id,
            'text': data['text'],
            'trigger_time': trigger_time.isoformat(),
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        
        save_data()
        await state.finish()
        
        await message.answer(f"✅ Уведомление создано!\n"
                           f"📝 {data['text']}\n"
                           f"⏰ Будет отправлено: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Создаем бэкап
        success = await create_backup("добавление уведомления")
        await show_backup_message(message, success)
        
    except ValueError:
        await message.answer("❌ Введите корректное число часов!")

@dp.message_handler(state=NotificationStates.waiting_for_days)
async def process_days(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)
        trigger_time = datetime.now() + timedelta(days=days)
        
        data = await state.get_data()
        notification_id = max(notifications.keys(), default=0) + 1
        
        notifications[notification_id] = {
            'id': notification_id,
            'text': data['text'],
            'trigger_time': trigger_time.isoformat(),
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        
        save_data()
        await state.finish()
        
        await message.answer(f"✅ Уведомление создано!\n"
                           f"📝 {data['text']}\n"
                           f"⏰ Будет отправлено: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        success = await create_backup("добавление уведомления")
        await show_backup_message(message, success)
        
    except ValueError:
        await message.answer("❌ Введите корректное число дней!")

@dp.message_handler(state=NotificationStates.waiting_for_months)
async def process_months(message: types.Message, state: FSMContext):
    try:
        months = int(message.text)
        # Простое добавление месяцев (можно улучшить)
        trigger_time = datetime.now() + timedelta(days=months*30)
        
        data = await state.get_data()
        notification_id = max(notifications.keys(), default=0) + 1
        
        notifications[notification_id] = {
            'id': notification_id,
            'text': data['text'],
            'trigger_time': trigger_time.isoformat(),
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        
        save_data()
        await state.finish()
        
        await message.answer(f"✅ Уведомление создано!\n"
                           f"📝 {data['text']}\n"
                           f"⏰ Будет отправлено: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        success = await create_backup("добавление уведомления")
        await show_backup_message(message, success)
        
    except ValueError:
        await message.answer("❌ Введите корректное число месяцев!")

@dp.message_handler(state=NotificationStates.waiting_for_specific_date)
async def process_specific_date(message: types.Message, state: FSMContext):
    try:
        trigger_time = datetime.strptime(message.text, '%Y-%m-%d %H:%M')
        
        if trigger_time <= datetime.now():
            await message.answer("❌ Дата должна быть в будущем!")
            return
        
        data = await state.get_data()
        notification_id = max(notifications.keys(), default=0) + 1
        
        notifications[notification_id] = {
            'id': notification_id,
            'text': data['text'],
            'trigger_time': trigger_time.isoformat(),
            'created_at': datetime.now().isoformat(),
            'status': 'active'
        }
        
        save_data()
        await state.finish()
        
        await message.answer(f"✅ Уведомление создано!\n"
                           f"📝 {data['text']}\n"
                           f"⏰ Будет отправлено: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        success = await create_backup("добавление уведомления")
        await show_backup_message(message, success)
        
    except ValueError:
        await message.answer("❌ Неверный формат даты! Используйте: ГГГГ-ММ-ДД ЧЧ:ММ")

@dp.message_handler(lambda message: message.text == "📝 Список уведомлений")
async def list_notifications(message: types.Message):
    if not notifications:
        await message.answer("📭 У вас нет активных уведомлений.")
        return
    
    active_notifications = {k: v for k, v in notifications.items() if v['status'] == 'active'}
    
    if not active_notifications:
        await message.answer("📭 Нет активных уведомлений.")
        return
    
    text = "📋 Ваши уведомления:\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for nid, notif in active_notifications.items():
        trigger_time = datetime.fromisoformat(notif['trigger_time'])
        text += f"🔔 #{nid}: {notif['text']}\n"
        text += f"⏰ {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        keyboard.add(InlineKeyboardButton(f"❌ Удалить #{nid}", callback_data=f"delete_{nid}"))
    
    await message.answer(text, reply_markup=keyboard)

@dp.message_handler(lambda message: message.text == "⚙️ Настройки")
async def settings_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(InlineKeyboardButton("📁 Изменить путь для бэкапов", callback_data="change_backup_path"))
    keyboard.add(InlineKeyboardButton("🕐 Настроить время проверки", callback_data="change_check_time"))
    keyboard.add(InlineKeyboardButton("🗑️ Настроить автоочистку", callback_data="change_auto_cleanup"))
    keyboard.add(InlineKeyboardButton("🔄 Создать бэкап сейчас", callback_data="backup_now"))
    keyboard.add(InlineKeyboardButton("📊 Текущие настройки", callback_data="show_settings"))
    
    await message.answer("⚙️ Настройки бота:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "change_backup_path")
async def change_backup_path(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("📁 Введите новый путь на Яндекс.Диске для бэкапов:\n"
                                       "Пример: /Мои документы/Backups")
    await SettingsStates.waiting_for_backup_path.set()
    await callback_query.answer()

@dp.message_handler(state=SettingsStates.waiting_for_backup_path)
async def process_backup_path(message: types.Message, state: FSMContext):
    new_path = message.text
    settings['backup_path'] = new_path
    save_data()
    
    await message.answer(f"✅ Путь для бэкапов изменен на: {new_path}")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "backup_now")
async def backup_now(callback_query: types.CallbackQuery):
    await callback_query.answer("🔄 Создание бэкапа...")
    success = await create_backup("ручной бэкап")
    
    if success:
        await callback_query.message.answer("✅ Бэкап успешно создан!")
    else:
        await callback_query.message.answer("❌ Не удалось создать бэкап!")

@dp.callback_query_handler(lambda c: c.data == "show_settings")
async def show_settings(callback_query: types.CallbackQuery):
    text = f"""
📊 ТЕКУЩИЕ НАСТРОЙКИ:

📁 Путь бэкапов: {settings['backup_path']}
🕐 Время проверки: {settings['check_time']}
🗑️ Автоочистка (дней): {settings['auto_cleanup_days']}
🔄 Повтор напоминания (часов): {settings['reminder_repeat_hours']}
📊 Всего уведомлений: {len(notifications)}
    """
    await callback_query.message.answer(text)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def delete_notification(callback_query: types.CallbackQuery):
    nid = int(callback_query.data.split('_')[1])
    
    if nid in notifications:
        notifications[nid]['status'] = 'deleted'
        save_data()
        await callback_query.message.answer(f"✅ Уведомление #{nid} удалено!")
        
        # Создаем бэкап
        success = await create_backup("удаление уведомления")
        await show_backup_message(callback_query.message, success)
    else:
        await callback_query.message.answer(f"❌ Уведомление #{nid} не найдено!")
    
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('complete_'))
async def complete_notification(callback_query: types.CallbackQuery):
    nid = int(callback_query.data.split('_')[1])
    
    if nid in notifications:
        notifications[nid]['status'] = 'completed'
        save_data()
        await callback_query.message.answer(f"✅ Уведомление #{nid} выполнено и удалено!")
        
        # Создаем бэкап
        success = await create_backup("выполнение уведомления")
        await show_backup_message(callback_query.message, success)
    
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('snooze_'))
async def snooze_notification(callback_query: types.CallbackQuery, state: FSMContext):
    nid = int(callback_query.data.split('_')[1])
    await state.update_data(snooze_nid=nid)
    await callback_query.message.answer("⏰ Через сколько часов напомнить? (введите число)")
    await NotificationStates.waiting_for_snooze_hours.set()
    await callback_query.answer()

@dp.message_handler(state=NotificationStates.waiting_for_snooze_hours)
async def process_snooze(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        data = await state.get_data()
        nid = data['snooze_nid']
        
        if nid in notifications:
            new_time = datetime.now() + timedelta(hours=hours)
            notifications[nid]['trigger_time'] = new_time.isoformat()
            save_data()
            await message.answer(f"✅ Уведомление отложено на {hours} часов!")
            
            # Создаем бэкап
            success = await create_backup("откладывание уведомления")
            await show_backup_message(message, success)
        
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Введите корректное число часов!")

@dp.callback_query_handler(lambda c: c.data == "update_database")
async def update_database(callback_query: types.CallbackQuery):
    # Здесь можно реализовать загрузку последнего бэкапа
    await callback_query.message.answer("🔄 Функция обновления базы из бэкапа будет добавлена позже...")
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_update")
async def cancel_update(callback_query: types.CallbackQuery):
    await callback_query.message.answer("✅ Обновление отменено")
    await callback_query.answer()

async def check_notifications():
    """Фоновая проверка уведомлений"""
    while True:
        now = datetime.now()
        to_notify = []
        
        for nid, notif in notifications.items():
            if notif['status'] == 'active':
                trigger_time = datetime.fromisoformat(notif['trigger_time'])
                if now >= trigger_time:
                    to_notify.append((nid, notif))
        
        for nid, notif in to_notify:
            # Отправляем уведомление
            keyboard = get_notification_keyboard(nid)
            await bot.send_message(
                ADMIN_ID,
                f"🔔 НАПОМИНАНИЕ!\n\n📝 {notif['text']}\n\nВыберите действие:",
                reply_markup=keyboard
            )
            
            # Если не нажата кнопка, повторяем через час
            async def repeat_notification():
                await asyncio.sleep(3600)  # 1 час
                if notifications.get(nid, {}).get('status') == 'active':
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔔 ПОВТОРНОЕ НАПОМИНАНИЕ!\n\n📝 {notif['text']}",
                        reply_markup=keyboard
                    )
            
            asyncio.create_task(repeat_notification())
            notifications[nid]['status'] = 'notified'
            save_data()
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

if __name__ == '__main__':
    # Загрузка данных
    load_data()
    
    # Запуск фоновых задач
    loop = asyncio.get_event_loop()
    loop.create_task(daily_check())
    loop.create_task(check_notifications())
    
    # Запуск бота
    executor.start_polling(dp, skip_updates=True)