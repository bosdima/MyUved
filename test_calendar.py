#!/usr/bin/env python3
"""
Тест CalDAV подключения к Яндекс Календарю
Правильный протокол для Яндекс Календаря - CalDAV, не REST API!
"""
import asyncio
import base64
import os
import xml.etree.ElementTree as ET
import uuid
from datetime import datetime, timedelta
import aiohttp
import pytz
from dotenv import load_dotenv

load_dotenv()

class Colors:
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    OKCYAN = '\033[96m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_success(msg): print(f"{Colors.OKGREEN}✅ {msg}{Colors.ENDC}")
def print_error(msg): print(f"{Colors.FAIL}❌ {msg}{Colors.ENDC}")
def print_warning(msg): print(f"{Colors.WARNING}⚠️ {msg}{Colors.ENDC}")
def print_info(msg): print(f"{Colors.OKCYAN}ℹ️ {msg}{Colors.ENDC}")
def print_header(msg): print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}\n{Colors.BOLD}{msg}{Colors.ENDC}\n{Colors.BOLD}{'='*60}{Colors.ENDC}")

async def test_caldav_connection():
    """Тест базового подключения к CalDAV"""
    print_header("1. ТЕСТ ПОДКЛЮЧЕНИЯ К CalDAV")
    
    token = os.getenv('YANDEX_TOKEN')
    email = os.getenv('YANDEX_EMAIL', '')
    
    if not token:
        print_error("YANDEX_TOKEN не найден в .env файле")
        print_info("Добавьте в .env: YANDEX_TOKEN=ваш_токен")
        return None, None
    
    print_info(f"Email: {email or '(не указан)'}")
    print_info(f"Token: {token[:15]}...{token[-10:] if len(token) > 30 else ''}")
    
    # Basic Auth для CalDAV: логин = email, пароль = OAuth токен
    auth_string = f"{email}:{token}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1"
    }
    
    # Простой PROPFIND запрос
    body = '''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
  <prop>
    <displayname/>
    <current-user-principal/>
  </prop>
</propfind>'''
    
    url = "https://caldav.yandex.ru/"
    print_info(f"\n🔍 PROPFIND {url}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request("PROPFIND", url, headers=headers, data=body.encode('utf-8'), timeout=15) as resp:
                print_info(f"Статус ответа: {resp.status}")
                
                if resp.status == 207:
                    print_success("✅ CalDAV сервер доступен! (207 Multi-Status)")
                    return headers, True
                elif resp.status == 401:
                    print_error("❌ Ошибка авторизации 401")
                    print_info("  → Проверьте правильность токена")
                    print_info("  → Убедитесь, что email указан верно")
                    return headers, False
                elif resp.status == 403:
                    print_error("❌ Доступ запрещен 403")
                    print_info("  → Проверьте права приложения в OAuth")
                    return headers, False
                else:
                    print_warning(f"⚠️ Неожиданный статус: {resp.status}")
                    text = await resp.text()
                    print_info(f"Ответ: {text[:300]}")
                    return headers, False
                    
        except aiohttp.ClientConnectorError as e:
            print_error(f"❌ Ошибка подключения: {e}")
            return None, False
        except asyncio.TimeoutError:
            print_error("❌ Таймаут подключения")
            return None, False
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return None, False


async def test_get_calendars(headers: dict):
    """Тест получения списка календарей"""
    print_header("2. ПОЛУЧЕНИЕ СПИСКА КАЛЕНДАРЕЙ")
    
    body = '''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <prop>
    <displayname/>
    <resourcetype/>
    <C:calendar-description/>
  </prop>
</propfind>'''
    
    url = "https://caldav.yandex.ru/"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request("PROPFIND", url, headers=headers, data=body.encode('utf-8'), timeout=15) as resp:
                if resp.status == 207:
                    text = await resp.text()
                    calendars = parse_calendars_xml(text)
                    
                    if calendars:
                        print_success(f"✅ Найдено календарей: {len(calendars)}")
                        for i, cal in enumerate(calendars, 1):
                            print_info(f"\n  📅 Календарь #{i}:")
                            print_info(f"     Название: {cal['name']}")
                            print_info(f"     Путь: {cal['path']}")
                            print_info(f"     Основной: {'Да' if cal.get('primary') else 'Нет'}")
                        return calendars
                    else:
                        print_warning("⚠️ Календари не найдены в ответе")
                        print_info("Ответ сервера (первые 800 символов):")
                        print(text[:800])
                        return []
                else:
                    print_error(f"❌ Ошибка: {resp.status}")
                    return None
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return None


def parse_calendars_xml(xml_text: str) -> list:
    """Парсит XML ответ от CalDAV сервера"""
    calendars = []
    try:
        namespaces = {
            'D': 'DAV:',
            'C': 'urn:ietf:params:xml:ns:caldav'
        }
        
        root = ET.fromstring(xml_text)
        
        for response in root.findall('.//D:response', namespaces):
            href = response.find('.//D:href', namespaces)
            displayname = response.find('.//D:displayname', namespaces)
            resourcetype = response.find('.//D:resourcetype', namespaces)
            
            if resourcetype is not None:
                calendar_tag = resourcetype.find('.//C:calendar', namespaces)
                if calendar_tag is not None and href is not None:
                    path = href.text
                    name = displayname.text if displayname is not None and displayname.text else "Календарь"
                    
                    calendars.append({
                        'path': path,
                        'name': name,
                        'primary': 'default' in (path or '').lower() or len(calendars) == 0
                    })
        
        return calendars
    except ET.ParseError as e:
        print_error(f"Ошибка парсинга XML: {e}")
        return []
    except Exception as e:
        print_error(f"Ошибка: {e}")
        return []


async def test_create_event(headers: dict, calendar_path: str = None):
    """Тест создания события в календаре"""
    print_header("3. ТЕСТ СОЗДАНИЯ СОБЫТИЯ")
    
    if not calendar_path:
        calendar_path = "/default/"
    
    if not calendar_path.endswith('/'):
        calendar_path += '/'
    
    # Создаем событие на завтра в 15:00
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    start_time = now + timedelta(days=1)
    start_time = start_time.replace(hour=15, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)
    
    event_uid = f"{uuid.uuid4()}@caldav-test"
    
    start_utc = start_time.astimezone(pytz.UTC)
    end_utc = end_time.astimezone(pytz.UTC)
    
    start_str = start_utc.strftime('%Y%m%dT%H%M%SZ')
    end_str = end_utc.strftime('%Y%m%dT%H%M%SZ')
    now_str = datetime.now(pytz.UTC).strftime('%Y%m%dT%H%M%SZ')
    
    ical_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CalDAV Test//RU
BEGIN:VEVENT
UID:{event_uid}
DTSTAMP:{now_str}
DTSTART:{start_str}
DTEND:{end_str}
SUMMARY:[ТЕСТ] Проверка CalDAV Яндекс
DESCRIPTION:Это тестовое событие для проверки работы CalDAV API Яндекс Календаря
END:VEVENT
END:VCALENDAR"""
    
    url = f"https://caldav.yandex.ru{calendar_path}{event_uid}.ics"
    
    # Для PUT запроса меняем Content-Type
    put_headers = headers.copy()
    put_headers["Content-Type"] = "text/calendar; charset=utf-8"
    put_headers.pop("Depth", None)
    
    print_info(f"Создание события в календаре: {calendar_path}")
    print_info(f"URL: {url}")
    print_info(f"Время: {start_time.strftime('%d.%m.%Y %H:%M')} - {end_time.strftime('%H:%M')}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.put(url, headers=put_headers, data=ical_data.encode('utf-8'), timeout=15) as resp:
                if resp.status in [201, 204]:
                    print_success(f"✅ Событие успешно создано! (статус {resp.status})")
                    print_success(f"   ID события: {event_uid}")
                    print_success(f"   Ссылка: https://calendar.yandex.ru/")
                    
                    # Удаляем тестовое событие
                    print_info("\n🗑️ Удаление тестового события...")
                    async with session.delete(url, headers=put_headers, timeout=15) as del_resp:
                        if del_resp.status in [200, 204]:
                            print_success("✅ Тестовое событие удалено")
                        else:
                            print_warning(f"⚠️ Не удалось удалить: {del_resp.status}")
                    
                    return True
                elif resp.status == 401:
                    print_error("❌ Ошибка авторизации")
                    return False
                elif resp.status == 403:
                    print_error("❌ Нет прав на запись")
                    return False
                elif resp.status == 404:
                    print_error(f"❌ Календарь не найден: {calendar_path}")
                    print_info("  → Попробуйте путь /default/ или /calendars/ID/")
                    return False
                else:
                    print_error(f"❌ Ошибка создания: {resp.status}")
                    text = await resp.text()
                    print_info(f"Ответ: {text[:300]}")
                    return False
                    
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return False


async def main():
    print_header("ДИАГНОСТИКА CalDAV ЯНДЕКС КАЛЕНДАРЯ")
    print_info(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Тест 1: Подключение
    headers, connected = await test_caldav_connection()
    
    if not connected or not headers:
        print_error("\n❌ Не удалось подключиться к CalDAV")
        print_header("РЕКОМЕНДАЦИИ")
        print_info("1. Убедитесь, что YANDEX_TOKEN действителен")
        print_info("2. Проверьте YANDEX_EMAIL в .env файле")
        print_info("3. Убедитесь, что в OAuth-приложении есть права:")
        print_info("   - calendar:read")
        print_info("   - calendar:write")
        return
    
    # Тест 2: Получение календарей
    calendars = await test_get_calendars(headers)
    
    if not calendars:
        print_warning("\n⚠️ Календари не найдены")
        print_header("РЕКОМЕНДАЦИИ")
        print_info("1. Перейдите на https://calendar.yandex.ru")
        print_info("2. Создайте календарь (кнопка «Добавить» → «Календарь»)")
        print_info("3. Задайте календарю цвет в настройках")
        return
    
    # Тест 3: Создание события
    calendar_path = calendars[0]['path'] if calendars else "/default/"
    print_info(f"\nИспользуем календарь: {calendar_path}")
    
    event_created = await test_create_event(headers, calendar_path)
    
    # Итоги
    print_header("ИТОГОВЫЙ ОТЧЕТ")
    
    print_info(f"📡 CalDAV подключение: {'✅ Успешно' if connected else '❌ Ошибка'}")
    print_info(f"📅 Найдено календарей: {len(calendars) if calendars else 0}")
    print_info(f"✏️ Создание события: {'✅ Успешно' if event_created else '❌ Ошибка'}")
    
    if connected and calendars and event_created:
        print_success("\n🎉 ВСЁ РАБОТАЕТ! CalDAV настроен правильно!")
        print_success("   Можно использовать синхронизацию с календарём в боте.")
    else:
        print_warning("\n⚠️ Есть проблемы с настройкой CalDAV")
        print_info("   Проверьте рекомендации выше.")


if __name__ == "__main__":
    asyncio.run(main())