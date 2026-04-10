#!/usr/bin/env python3
"""
Автоматический тестовый скрипт для диагностики подключения к Яндекс Календарю
Не требует ввода данных - использует токен из .env или user_tokens.json
"""

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlencode

import aiohttp
import pytz
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')
YANDEX_TOKEN = os.getenv('YANDEX_TOKEN')

# Цветной вывод в консоль
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_section(title: str):
    print(f"\n{Colors.BOLD}{Colors.OKBLUE}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKCYAN} {title}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKBLUE}{'='*60}{Colors.ENDC}")

def print_success(msg: str):
    print(f"{Colors.OKGREEN}✅ {msg}{Colors.ENDC}")

def print_error(msg: str):
    print(f"{Colors.FAIL}❌ {msg}{Colors.ENDC}")

def print_warning(msg: str):
    print(f"{Colors.WARNING}⚠️ {msg}{Colors.ENDC}")

def print_info(msg: str):
    print(f"{Colors.OKCYAN}ℹ️ {msg}{Colors.ENDC}")

def print_debug(msg: str):
    print(f"  🔍 {msg}")

# Список URL для тестирования
CALENDAR_API_URLS = [
    "https://api.calendar.yandex.net/calendar/v1",
    "https://calendar.yandex.ru/api/v1",
    "https://caldav.yandex.ru/calendars",
]

OAUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"


def load_token() -> tuple:
    """Загружает токен из разных источников"""
    token = None
    source = None
    
    # 1. Из .env файла
    if YANDEX_TOKEN:
        token = YANDEX_TOKEN
        source = ".env файл (YANDEX_TOKEN)"
        print_success(f"Токен найден в {source}")
        return token, source
    
    # 2. Из user_tokens.json
    token_file = 'user_tokens.json'
    if os.path.exists(token_file):
        try:
            with open(token_file, 'r') as f:
                data = json.load(f)
                for user_id, tok in data.items():
                    token = tok
                    source = f"user_tokens.json (user_id={user_id})"
                    print_success(f"Токен найден в {source}")
                    return token, source
        except Exception as e:
            print_warning(f"Ошибка чтения {token_file}: {e}")
    
    # 3. Из config.json (если есть поле yandex_token)
    config_file = 'config.json'
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                token = config.get('yandex_token')
                if token:
                    source = "config.json"
                    print_success(f"Токен найден в {source}")
                    return token, source
        except Exception as e:
            print_warning(f"Ошибка чтения {config_file}: {e}")
    
    print_error("Токен не найден ни в одном источнике!")
    print_info("Добавьте YANDEX_TOKEN=ваш_токен в .env файл")
    return None, None


async def test_network_connectivity():
    """Тест базового подключения к интернету и серверам Яндекса"""
    print_section("1. ТЕСТ СЕТЕВОГО ПОДКЛЮЧЕНИЯ")
    
    test_urls = [
        ("Яндекс (главная)", "https://yandex.ru"),
        ("Яндекс OAuth", "https://oauth.yandex.ru"),
        ("Яндекс Календарь (веб)", "https://calendar.yandex.ru"),
        ("API Календаря", "https://api.calendar.yandex.net"),
    ]
    
    results = {}
    async with aiohttp.ClientSession() as session:
        for name, url in test_urls:
            try:
                async with session.get(url, timeout=10, allow_redirects=True) as response:
                    if response.status < 400:
                        print_success(f"{name}: доступен (код {response.status})")
                        results[name] = True
                    elif response.status == 400:
                        print_warning(f"{name}: код 400 (возможно, требуется авторизация)")
                        results[name] = True  # 400 тоже означает что сервер ответил
                    else:
                        print_warning(f"{name}: код ответа {response.status}")
                        results[name] = False
            except asyncio.TimeoutError:
                print_error(f"{name}: таймаут подключения")
                results[name] = False
            except aiohttp.ClientConnectorError as e:
                print_error(f"{name}: ошибка подключения - {e}")
                results[name] = False
            except Exception as e:
                print_error(f"{name}: неизвестная ошибка - {e}")
                results[name] = False
    
    return results


async def test_token_info(token: str):
    """Получает информацию о токене"""
    print_section("2. ИНФОРМАЦИЯ О ТОКЕНЕ")
    
    if not token:
        print_error("Токен не предоставлен")
        return None
    
    print_info(f"Токен: {token[:15]}...{token[-10:] if len(token) > 30 else ''}")
    print_info(f"Длина токена: {len(token)} символов")
    
    # Проверяем формат токена
    if token.startswith('y0_') or token.startswith('y1_'):
        print_success("Формат токена: новый (y0_/y1_)")
    elif token.startswith('AQAAAA'):
        print_success("Формат токена: старый (AQAAAA)")
    else:
        print_warning(f"Неизвестный формат токена: {token[:10]}...")
    
    # Пытаемся получить информацию о токене через OAuth
    headers = {"Authorization": f"OAuth {token}"}
    
    async with aiohttp.ClientSession() as session:
        # Проверяем через /tokeninfo если доступно
        try:
            url = "https://oauth.yandex.ru/tokeninfo"
            params = {"oauth_token": token}
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    print_success("Информация о токене получена:")
                    print_debug(f"  Client ID: {data.get('client_id', 'N/A')[:20]}...")
                    print_debug(f"  Scope: {data.get('scope', 'N/A')}")
                    print_debug(f"  Expires: {data.get('expires_in', 'N/A')} сек")
                    return data
                else:
                    print_warning(f"Не удалось получить информацию о токене: {response.status}")
        except Exception as e:
            print_debug(f"Ошибка tokeninfo: {e}")
    
    return None


async def test_api_endpoints(token: str):
    """Тест доступности различных API эндпоинтов календаря"""
    print_section("3. ТЕСТ API ЭНДПОИНТОВ КАЛЕНДАРЯ")
    
    if not token:
        print_error("Токен не предоставлен")
        return None
    
    headers = {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    working_url = None
    calendars_data = None
    
    async with aiohttp.ClientSession() as session:
        for base_url in CALENDAR_API_URLS:
            print_info(f"\nТестирование: {base_url}")
            
            # Тест 1: Получение списка календарей
            for path in ["/calendars", "/calendars/primary", ""]:
                url = f"{base_url}{path}" if path else base_url
                print_debug(f"GET {url}")
                
                try:
                    async with session.get(url, headers=headers, timeout=15) as response:
                        print_debug(f"Статус: {response.status}")
                        
                        if response.status == 200:
                            try:
                                data = await response.json()
                                print_success(f"Успешный ответ от {url}!")
                                
                                # Проверяем формат ответа
                                if 'items' in data:
                                    calendars = data.get('items', [])
                                    print_success(f"Найдено календарей: {len(calendars)}")
                                    for cal in calendars[:5]:
                                        print_debug(f"  - {cal.get('summary', 'Без названия')} (ID: {cal.get('id', 'unknown')})")
                                    working_url = base_url
                                    calendars_data = data
                                    break
                                elif 'calendars' in data:
                                    calendars = data.get('calendars', [])
                                    print_success(f"Найдено календарей: {len(calendars)}")
                                    working_url = base_url
                                    calendars_data = data
                                    break
                                elif isinstance(data, list):
                                    print_success(f"Получен список из {len(data)} элементов")
                                    working_url = base_url
                                    break
                                else:
                                    print_warning(f"Неизвестный формат ответа: {list(data.keys())}")
                                    print_debug(f"Ответ: {json.dumps(data, indent=2)[:300]}")
                                    
                            except Exception as e:
                                print_error(f"Ошибка парсинга JSON: {e}")
                                text = await response.text()
                                print_debug(f"Текст ответа: {text[:200]}")
                        
                        elif response.status == 401:
                            print_error("Ошибка 401: Токен недействителен или истек")
                        elif response.status == 403:
                            print_error("Ошибка 403: Недостаточно прав")
                            print_info("  Необходимы права: calendar:read и calendar:write")
                        elif response.status == 404:
                            print_warning("Ошибка 404: Эндпоинт не найден")
                        elif response.status == 400:
                            print_warning(f"Ошибка 400: Неверный запрос")
                            text = await response.text()
                            if 'oauth' in text.lower():
                                print_error("  Проблема с OAuth-токеном!")
                            print_debug(f"Ответ: {text[:200]}")
                        else:
                            print_warning(f"Неожиданный статус: {response.status}")
                            text = await response.text()
                            print_debug(f"Ответ: {text[:200]}")
                            
                except asyncio.TimeoutError:
                    print_error("Таймаут подключения")
                except aiohttp.ClientConnectorError as e:
                    print_error(f"Ошибка подключения: {e}")
                except Exception as e:
                    print_error(f"Неизвестная ошибка: {e}")
            
            if working_url:
                break
    
    return working_url, calendars_data


async def test_create_event(token: str, calendar_id: str = "primary", base_url: str = None):
    """Тест создания тестового события в календаре"""
    print_section("4. ТЕСТ СОЗДАНИЯ СОБЫТИЯ")
    
    if not token:
        print_error("Токен не предоставлен")
        return False
    
    headers = {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json"
    }
    
    # Если base_url не указан, пробуем все
    urls_to_try = [base_url] if base_url else [
        "https://api.calendar.yandex.net/calendar/v1",
        "https://calendar.yandex.ru/api/v1",
    ]
    urls_to_try = [u for u in urls_to_try if u]  # Убираем None
    
    # Время события: через 1 час, длительность 1 час
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    start_time = now + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    event_data = {
        "summary": "[ТЕСТ] Проверка API Яндекс Календаря",
        "description": "Это событие создано автоматически для проверки работы API. Можно удалить.",
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "Europe/Moscow"
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "Europe/Moscow"
        }
    }
    
    print_info(f"Время события: {start_time.strftime('%d.%m.%Y %H:%M')} - {end_time.strftime('%H:%M')}")
    
    async with aiohttp.ClientSession() as session:
        for base_url in urls_to_try:
            url = f"{base_url}/calendars/{calendar_id}/events"
            print_info(f"\nПопытка создать событие через: POST {url}")
            print_debug(f"Данные: {json.dumps(event_data, indent=2, ensure_ascii=False)}")
            
            try:
                async with session.post(url, headers=headers, json=event_data, timeout=15) as response:
                    print_debug(f"Статус: {response.status}")
                    
                    if response.status in [200, 201]:
                        data = await response.json()
                        event_id = data.get('id')
                        print_success(f"✅ Событие успешно создано!")
                        print_success(f"   ID: {event_id}")
                        print_success(f"   Ссылка: https://calendar.yandex.ru/event?event_id={event_id}")
                        
                        # Пытаемся удалить тестовое событие
                        print_info("\nУдаление тестового события...")
                        delete_url = f"{base_url}/calendars/{calendar_id}/events/{event_id}"
                        async with session.delete(delete_url, headers=headers, timeout=15) as del_response:
                            if del_response.status in [200, 204]:
                                print_success("✅ Тестовое событие удалено")
                            else:
                                print_warning(f"⚠️ Не удалось удалить событие: {del_response.status}")
                                print_info(f"   Удалите вручную: https://calendar.yandex.ru/event?event_id={event_id}")
                        
                        return True
                    else:
                        error_text = await response.text()
                        print_error(f"Ошибка: {response.status}")
                        print_debug(f"Ответ: {error_text[:500]}")
                        
                        # Анализируем ошибку
                        if response.status == 401:
                            print_error("  → Токен недействителен. Получите новый токен.")
                        elif response.status == 403:
                            print_error("  → Нет прав на запись. Проверьте права calendar:write в OAuth-приложении.")
                        elif response.status == 404:
                            print_error("  → Календарь не найден. Используйте calendar_id='primary' или создайте календарь.")
                        elif response.status == 400:
                            if 'invalid_grant' in error_text:
                                print_error("  → Токен истек или отозван.")
                            elif 'calendar' in error_text.lower():
                                print_error("  → Проблема с календарем. Проверьте его существование.")
                        
            except asyncio.TimeoutError:
                print_error("Таймаут подключения")
            except aiohttp.ClientConnectorError as e:
                print_error(f"Ошибка подключения: {e}")
            except Exception as e:
                print_error(f"Неизвестная ошибка: {e}")
    
    return False


async def test_calendar_list(token: str, base_url: str = None):
    """Детальный тест получения списка календарей"""
    print_section("5. ДЕТАЛЬНЫЙ ТЕСТ КАЛЕНДАРЕЙ")
    
    if not token:
        print_error("Токен не предоставлен")
        return
    
    headers = {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json"
    }
    
    urls_to_try = [base_url] if base_url else [
        "https://api.calendar.yandex.net/calendar/v1",
        "https://calendar.yandex.ru/api/v1",
    ]
    urls_to_try = [u for u in urls_to_try if u]
    
    async with aiohttp.ClientSession() as session:
        for base_url in urls_to_try:
            url = f"{base_url}/calendars"
            print_info(f"\nЗапрос списка календарей: GET {url}")
            
            try:
                async with session.get(url, headers=headers, timeout=15) as response:
                    print_debug(f"Статус: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        print_success("Ответ получен успешно!")
                        
                        # Детальный разбор ответа
                        print_info(f"Ключи в ответе: {list(data.keys())}")
                        
                        calendars = None
                        if 'items' in data:
                            calendars = data['items']
                            print_success(f"Найдено календарей: {len(calendars)}")
                        elif 'calendars' in data:
                            calendars = data['calendars']
                            print_success(f"Найдено календарей: {len(calendars)}")
                        elif isinstance(data, list):
                            calendars = data
                            print_success(f"Найдено календарей: {len(calendars)}")
                        
                        if calendars:
                            for i, cal in enumerate(calendars[:10]):
                                print_debug(f"  [{i+1}] {cal.get('summary', 'Без названия')}")
                                print_debug(f"      ID: {cal.get('id', 'N/A')}")
                                print_debug(f"      Primary: {cal.get('primary', False)}")
                                print_debug(f"      Access Role: {cal.get('accessRole', 'N/A')}")
                            
                            if len(calendars) == 0:
                                print_warning("Список календарей пуст!")
                                print_info("  → Перейдите на calendar.yandex.ru и создайте календарь")
                                print_info("  → Задайте календарю цвет в настройках")
                        else:
                            print_warning("Не удалось найти календари в ответе")
                            print_debug(f"Ответ: {json.dumps(data, indent=2)[:500]}")
                        
                        return calendars
                    else:
                        text = await response.text()
                        print_error(f"Ошибка: {response.status}")
                        print_debug(f"Ответ: {text[:300]}")
                        
            except Exception as e:
                print_error(f"Ошибка: {e}")


def print_summary(network_results: dict, api_working: bool, event_created: bool, calendars: list):
    """Выводит итоговый отчет"""
    print_section("ИТОГОВЫЙ ОТЧЕТ")
    
    print_info("\n📊 Результаты диагностики:")
    
    # Сетевое подключение
    print("\n🌐 Сетевое подключение:")
    for name, ok in network_results.items():
        if ok:
            print_success(f"  {name}")
        else:
            print_error(f"  {name}")
    
    # API
    print("\n📡 API Календаря:")
    if api_working:
        print_success("  API доступен")
    else:
        print_error("  API недоступен")
    
    # Календари
    print("\n📅 Календари:")
    if calendars:
        print_success(f"  Найдено календарей: {len(calendars)}")
        for cal in calendars[:3]:
            print_info(f"    - {cal.get('summary', 'Без названия')}")
    else:
        print_error("  Календари не найдены")
    
    # Создание события
    print("\n✏️ Создание события:")
    if event_created:
        print_success("  Успешно")
    else:
        print_error("  Не удалось")
    
    # Рекомендации
    print_section("РЕКОМЕНДАЦИИ")
    
    if not calendars:
        print_warning("1. У вас нет календарей в Яндекс Календаре!")
        print_info("   → Перейдите на https://calendar.yandex.ru")
        print_info("   → Создайте новый календарь (кнопка «Добавить» → «Календарь»)")
        print_info("   → Задайте календарю любой цвет в настройках")
    
    if not api_working:
        print_warning("2. API Календаря недоступен")
        print_info("   → Проверьте права доступа в OAuth-приложении:")
        print_info("     https://oauth.yandex.ru/")
        print_info("   → Необходимые права: calendar:read и calendar:write")
        print_info("   → Убедитесь, что приложение не в режиме «Тестирование»")
    
    if not event_created and api_working and calendars:
        print_warning("3. Не удалось создать событие, хотя API и календари доступны")
        print_info("   → Проверьте права calendar:write в OAuth-приложении")
        print_info("   → Возможно, календарь доступен только для чтения")
        print_info("   → Попробуйте использовать calendar_id='primary'")
    
    print_info("\n4. Если проблема не решается:")
    print_info("   → Получите новый токен через кнопку «Авторизация» в боте")
    print_info("   → Проверьте актуальность документации: https://yandex.ru/dev/calendar/")


async def main():
    print_section("ДИАГНОСТИКА ЯНДЕКС КАЛЕНДАРЯ (АВТО)")
    print(f"Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version}")
    
    # Проверяем переменные окружения
    print_section("0. ПРОВЕРКА КОНФИГУРАЦИИ")
    print_info(f"CLIENT_ID: {'Задан' if CLIENT_ID else 'НЕ ЗАДАН'}")
    print_info(f"CLIENT_SECRET: {'Задан' if CLIENT_SECRET else 'НЕ ЗАДАН'}")
    print_info(f"REDIRECT_URI: {REDIRECT_URI}")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print_error("CLIENT_ID или CLIENT_SECRET не заданы в .env файле!")
        return
    
    # Загружаем токен
    token, token_source = load_token()
    
    if not token:
        print_error("\nТокен не найден. Добавьте YANDEX_TOKEN в .env файл или авторизуйтесь через бота.")
        return
    
    # 1. Тест сети
    network_results = await test_network_connectivity()
    
    # 2. Информация о токене
    token_info = await test_token_info(token)
    
    # 3. Тест API эндпоинтов
    working_url, calendars_data = await test_api_endpoints(token)
    
    api_working = working_url is not None
    
    # 4. Тест получения календарей
    calendars = await test_calendar_list(token, working_url)
    
    # 5. Тест создания события
    calendar_id = "primary"
    if calendars and len(calendars) > 0:
        calendar_id = calendars[0].get('id', 'primary')
        print_info(f"\nИспользуем calendar_id: {calendar_id}")
    
    event_created = await test_create_event(token, calendar_id, working_url)
    
    # Итоги
    print_summary(network_results, api_working, event_created, calendars)
    
    # Сохраняем результаты в файл
    results = {
        "timestamp": datetime.now().isoformat(),
        "network": network_results,
        "api_working": api_working,
        "working_url": working_url,
        "calendars_count": len(calendars) if calendars else 0,
        "event_created": event_created,
        "token_source": token_source
    }
    
    with open('calendar_test_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print_success("\n📁 Результаты сохранены в calendar_test_results.json")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        traceback.print_exc()