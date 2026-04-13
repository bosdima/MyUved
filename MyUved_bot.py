import asyncio
import base64
import os
import logging
import re
import uuid
import json
from datetime import datetime, timedelta

import aiohttp
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv
from xml.etree import ElementTree as ET

# Версия бота
BOT_VERSION = "2.0.7"
BOT_VERSION_DATE = "13.04.2026"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID')) if os.getenv('ADMIN_ID') else None
YANDEX_EMAIL = os.getenv('YANDEX_EMAIL')
YANDEX_APP_PASSWORD = os.getenv('YANDEX_APP_PASSWORD')

print(f"\n{'='*50}")
print(f"ЗАПУСК БОТА v{BOT_VERSION}")
print(f"ADMIN_ID: {ADMIN_ID}")
print(f"YANDEX_EMAIL: {YANDEX_EMAIL}")
print(f"{'='*50}\n")

if not all([BOT_TOKEN, ADMIN_ID, YANDEX_EMAIL, YANDEX_APP_PASSWORD]):
    logger.error("❌ Не все переменные окружения заданы!")
    exit(1)

# Инициализация бота
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Константы
CALDAV_URL = f"https://caldav.yandex.ru/calendars/{YANDEX_EMAIL}"
EVENTS_URL = f"{CALDAV_URL}/events-default/"
TIMEZONE = 'Europe/Moscow'
TZ = pytz.timezone(TIMEZONE)

# Временное хранилище для маппинга callback_data -> event_id
event_map = {}

# Состояния FSM
class EventStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_date = State()
    waiting_for_edit_title = State()
    waiting_for_edit_date = State()

# Простой клиент CalDAV
class CalDAVClient:
    def __init__(self):
        self.auth = base64.b64encode(f"{YANDEX_EMAIL}:{YANDEX_APP_PASSWORD}".encode()).decode()

    async def get_events(self, days=30):
        print(f"[CalDAV] Запрос событий на {days} дней...")
        try:
            now = datetime.now(TZ)
            start = now.astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
            end = (now + timedelta(days=days)).astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
            
            xml = f"""<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:prop><C:calendar-data/></D:prop>
    <C:filter>
        <C:comp-filter name="VCALENDAR">
            <C:comp-filter name="VEVENT">
                <C:time-range start="{start}" end="{end}"/>
            </C:comp-filter>
        </C:comp-filter>
    </C:filter>
</C:calendar-query>"""
            
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    'REPORT',
                    EVENTS_URL,
                    headers={
                        "Authorization": f"Basic {self.auth}",
                        "Content-Type": "application/xml",
                        "Depth": "1"
                    },
                    data=xml,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 207:
                        text = await resp.text()
                        events = self.parse_events(text)
                        print(f"[CalDAV] Распарсено событий: {len(events)}")
                        return events
                    else:
                        print(f"[CalDAV] Ошибка, статус: {resp.status}")
                        return []
        except Exception as e:
            print(f"[CalDAV] ИСКЛЮЧЕНИЕ: {e}")
            return []

    def parse_events(self, xml_text):
        events = []
        try:
            ns = {'D': 'DAV:', 'C': 'urn:ietf:params:xml:ns:caldav'}
            root = ET.fromstring(xml_text)
            
            for resp in root.findall('.//D:response', ns):
                href = resp.find('.//D:href', ns)
                data = resp.find('.//C:calendar-data', ns)
                
                if data is not None and data.text:
                    event = self.parse_ics(data.text)
                    if event:
                        # Извлекаем UID из href
                        if href is not None:
                            href_text = href.text
                            event['href'] = href_text
                            match = re.search(r'/([^/]+)\.ics$', href_text)
                            if match:
                                event['uid'] = match.group(1)
                        events.append(event)
        except Exception as e:
            print(f"[CalDAV] Ошибка парсинга XML: {e}")
        
        # Сортируем по дате
        events.sort(key=lambda x: x.get('start') or datetime.max.replace(tzinfo=pytz.UTC))
        return events

    def parse_ics(self, ics_text):
        event = {'summary': 'Без названия'}
        
        lines = ics_text.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('SUMMARY:'):
                val = line[8:]
                val = val.replace('\\,', ',').replace('\\n', '\n').replace('\\;', ';')
                event['summary'] = val
            elif line.startswith('UID:'):
                event['uid'] = line[4:]
            elif line.startswith('DTSTART'):
                parts = line.split(':')
                if len(parts) > 1:
                    dt_str = parts[-1]
                    if 'T' in dt_str:
                        try:
                            if dt_str.endswith('Z'):
                                dt = datetime.strptime(dt_str[:15], '%Y%m%dT%H%M%S')
                                event['start'] = pytz.UTC.localize(dt)
                            else:
                                dt = datetime.strptime(dt_str[:15], '%Y%m%dT%H%M%S')
                                event['start'] = TZ.localize(dt)
                        except:
                            pass
        
        return event

    async def create_event(self, summary, start_time):
        try:
            uid = str(uuid.uuid4())
            
            if start_time.tzinfo is None:
                start_time = TZ.localize(start_time)
            end_time = start_time + timedelta(hours=1)
            
            dtstart = start_time.astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
            dtend = end_time.astimezone(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
            
            # Экранируем спецсимволы
            summary = summary.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')
            
            ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:{uid}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
END:VEVENT
END:VCALENDAR"""
            
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{EVENTS_URL}{uid}.ics",
                    headers={
                        "Authorization": f"Basic {self.auth}",
                        "Content-Type": "text/calendar"
                    },
                    data=ics.encode('utf-8'),
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    return resp.status in [200, 201, 204], uid
        except Exception as e:
            return False, str(e)

    async def delete_event(self, uid):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{EVENTS_URL}{uid}.ics",
                    headers={"Authorization": f"Basic {self.auth}"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    return resp.status in [200, 204]
        except:
            return False

def format_datetime(dt):
    if dt is None:
        return "без даты"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = TZ.localize(dt)
        local = dt.astimezone(TZ)
        
        now = datetime.now(TZ)
        if local.date() == now.date():
            return f"Сегодня в {local.strftime('%H:%M')}"
        elif local.date() == now.date() + timedelta(days=1):
            return f"Завтра в {local.strftime('%H:%M')}"
        else:
            return local.strftime('%d.%m.%Y в %H:%M')
    return str(dt)

def parse_datetime_input(date_str):
    date_str = date_str.strip()
    now = datetime.now(TZ)
    
    # ДД.ММ.ГГГГ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        d, m, y, H, M = map(int, match.groups())
        try:
            return TZ.localize(datetime(y, m, d, H, M))
        except:
            return None
    
    # ДД.ММ ЧЧ:ММ
    match = re.match(r'^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        d, m, H, M = map(int, match.groups())
        try:
            dt = TZ.localize(datetime(now.year, m, d, H, M))
            if dt < now:
                dt = TZ.localize(datetime(now.year + 1, m, d, H, M))
            return dt
        except:
            return None
    
    # Завтра ЧЧ:ММ
    match = re.match(r'^[Зз]автра\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        H, M = map(int, match.groups())
        tomorrow = now + timedelta(days=1)
        return TZ.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, H, M))
    
    # Сегодня ЧЧ:ММ
    match = re.match(r'^[Сс]егодня\s+(\d{1,2}):(\d{2})$', date_str)
    if match:
        H, M = map(int, match.groups())
        dt = TZ.localize(datetime(now.year, now.month, now.day, H, M))
        if dt < now:
            dt = TZ.localize(datetime(now.year, now.month, now.day + 1, H, M))
        return dt
    
    return None

def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("📋 Показать заметки"),
        KeyboardButton("➕ Добавить заметку")
    )
    keyboard.add(KeyboardButton("ℹ️ Статус"))
    return keyboard

# ============ ОБРАБОТЧИКИ ============

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    print(f"[START] От {message.from_user.id}")
    
    if message.from_user.id != ADMIN_ID:
        await message.reply("❌ Нет доступа")
        return
    
    await message.reply(
        f"🤖 **Бот для Яндекс.Календаря**\n"
        f"📌 v{BOT_VERSION} ({BOT_VERSION_DATE})\n\n"
        f"✅ Подключено\n"
        f"📧 `{YANDEX_EMAIL}`",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

@dp.message_handler(lambda m: m.text == "📋 Показать заметки")
async def show_events(message: types.Message):
    print(f"\n[SHOW] НАЖАТА КНОПКА 'Показать заметки'")
    
    msg = await message.reply("⏳ Загружаю заметки...")
    
    try:
        client = CalDAVClient()
        events = await client.get_events(days=30)
        
        if not events:
            await msg.edit_text("📭 **Нет заметок на ближайшие 30 дней**", parse_mode='Markdown')
            return
        
        # Очищаем старые маппинги
        event_map.clear()
        
        text = f"📋 **Найдено заметок: {len(events)}**\n\n"
        
        # Группируем по дате
        by_date = {}
        for e in events:
            start = e.get('start')
            if start and isinstance(start, datetime):
                date_key = start.astimezone(TZ).date()
            else:
                date_key = None
            
            if date_key not in by_date:
                by_date[date_key] = []
            by_date[date_key].append(e)
        
        # Формируем текст
        for date_key in sorted(by_date.keys(), key=lambda x: x or datetime.max.date()):
            if date_key is None:
                text += "📌 **Без даты:**\n"
            else:
                today = datetime.now(TZ).date()
                tomorrow = today + timedelta(days=1)
                
                if date_key == today:
                    text += "🟢 **Сегодня:**\n"
                elif date_key == tomorrow:
                    text += "🟡 **Завтра:**\n"
                else:
                    text += f"📅 **{date_key.strftime('%d.%m.%Y')}:**\n"
            
            for e in by_date[date_key][:10]:
                summary = e.get('summary', 'Без названия')[:40]
                start = e.get('start')
                time_str = start.astimezone(TZ).strftime('%H:%M') if start and isinstance(start, datetime) else ""
                text += f"   • {summary}"
                if time_str:
                    text += f" ({time_str})"
                text += "\n"
        
        # Создаем клавиатуру с короткими callback_data
        keyboard = InlineKeyboardMarkup(row_width=1)
        
        for i, e in enumerate(events[:15]):
            uid = e.get('uid')
            if not uid:
                continue
            
            # Сохраняем в маппинг
            callback_key = f"del_{i}"
            event_map[callback_key] = uid
            
            summary = e.get('summary', '?')[:25]
            start = e.get('start')
            time_str = start.astimezone(TZ).strftime('%H:%M') if start and isinstance(start, datetime) else ""
            
            btn_text = f"🗑️ {summary}"
            if time_str:
                btn_text += f" ({time_str})"
            
            keyboard.add(InlineKeyboardButton(btn_text, callback_data=callback_key))
        
        await msg.edit_text(text, reply_markup=keyboard, parse_mode='Markdown')
        print(f"[SHOW] Ответ отправлен, {len(events)} событий")
        
    except Exception as e:
        print(f"[SHOW] ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

@dp.message_handler(lambda m: m.text == "➕ Добавить заметку")
async def add_event_start(message: types.Message):
    print(f"[ADD] Нажата кнопка Добавить")
    
    await message.reply(
        "✏️ **Введите текст заметки:**\n\n"
        "💡 Для отмены отправьте /cancel",
        parse_mode='Markdown'
    )
    await EventStates.waiting_for_title.set()

@dp.message_handler(state=EventStates.waiting_for_title)
async def add_event_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    
    await message.reply(
        "📅 **Введите дату и время:**\n\n"
        "📝 Примеры:\n"
        "• `25.12.2026 14:30`\n"
        "• `25.12 14:30`\n"
        "• `Завтра 09:00`\n"
        "• `Сегодня 18:00`\n\n"
        "💡 Для отмены отправьте /cancel",
        parse_mode='Markdown'
    )
    await EventStates.waiting_for_date.set()

@dp.message_handler(state=EventStates.waiting_for_date)
async def add_event_date(message: types.Message, state: FSMContext):
    dt = parse_datetime_input(message.text.strip())
    
    if not dt:
        await message.reply("❌ **Неверный формат даты.** Попробуйте ещё раз.", parse_mode='Markdown')
        return
    
    data = await state.get_data()
    title = data.get('title')
    
    msg = await message.reply("⏳ Создаю заметку...")
    
    client = CalDAVClient()
    ok, _ = await client.create_event(title, dt)
    
    if ok:
        await msg.edit_text(
            f"✅ **Заметка создана!**\n\n"
            f"📝 {title}\n"
            f"🕐 {format_datetime(dt)}",
            parse_mode='Markdown'
        )
    else:
        await msg.edit_text("❌ **Ошибка создания заметки**", parse_mode='Markdown')
    
    await state.finish()

@dp.message_handler(lambda m: m.text == "ℹ️ Статус")
async def show_status(message: types.Message):
    print(f"[STATUS] Нажата кнопка Статус")
    
    client = CalDAVClient()
    events = await client.get_events(days=7)
    
    await message.reply(
        f"📊 **Статус**\n\n"
        f"📧 `{YANDEX_EMAIL}`\n"
        f"📅 Событий на неделю: **{len(events)}**\n"
        f"🕐 Текущее время: {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}\n"
        f"🌍 Часовой пояс: {TIMEZONE}\n"
        f"🤖 Версия: v{BOT_VERSION}",
        parse_mode='Markdown'
    )

@dp.callback_query_handler(lambda c: c.data.startswith('del_'))
async def delete_event(callback: types.CallbackQuery):
    callback_key = callback.data
    uid = event_map.get(callback_key)
    
    print(f"[DELETE] Удаление: {callback_key} -> {uid}")
    
    if not uid:
        await callback.answer("❌ Событие не найдено", show_alert=True)
        return
    
    client = CalDAVClient()
    ok = await client.delete_event(uid)
    
    if ok:
        # Удаляем из маппинга
        del event_map[callback_key]
        await callback.message.edit_text("✅ **Заметка удалена**", parse_mode='Markdown')
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)

@dp.message_handler(commands=['cancel'], state='*')
async def cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.reply("❌ **Операция отменена**", parse_mode='Markdown')

async def on_startup(dp):
    print(f"\n{'='*50}")
    print(f"✅ БОТ ГОТОВ К РАБОТЕ v{BOT_VERSION}")
    print(f"{'='*50}\n")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)