#!/usr/bin/env python3
"""
Тестовый скрипт для диагностики подключения к Яндекс Календарю
"""

import asyncio
import json
import sys
import traceback
from datetime import datetime, timedelta
from urllib.parse import urlencode

import aiohttp
import pytz
from dotenv import load_dotenv
import os

# Загружаем переменные окружения
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')

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
    "https://caldav.yandex.ru",
]

OAUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"


async def test_network_connectivity():
    """Тест базового подключения к интернету и серверам Яндекса"""
    print_section("1. ТЕСТ СЕТЕВОГО ПОДКЛЮЧЕНИЯ")
    
    test_urls = [
        ("Яндекс (главная)", "https://yandex.ru"),
        ("Яндекс OAuth", "https://oauth.yandex.ru"),
        ("Яндекс Календарь (веб)", "https://calendar.yandex.ru"),
    ]
    
    async with aiohttp.ClientSession() as session:
        for name, url in test_urls:
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status < 400:
                        print_success(f"{name}: доступен (код {response.status})")
                    else:
                        print_warning(f"{name}: код ответа {response.status}")
            except asyncio.TimeoutError:
                print_error(f"{name}: таймаут подключения")
            except aiohttp.ClientConnectorError as e:
                print_error(f"{name}: ошибка подключения - {e}")
            except Exception as e:
                print_error(f"{name}: неизвестная ошибка - {e}")


async def test_api_endpoints(token: str = None):
    """Тест доступности различных API эндпоинтов календаря"""
    print_section("2. ТЕСТ API ЭНДПОИНТОВ КАЛЕНДАРЯ")
    
    if not token:
        print_warning("Токен не предоставлен, тест API будет неполным")
        headers = {}
    else:
        headers = {
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json"
        }
        print_info(f"Токен: {token[:20]}...{token[-10:] if len(token) > 30 else ''}")
    
    async with aiohttp.ClientSession() as session:
        for base_url in CALENDAR_API_URLS:
            print_info(f"\nТестирование базового URL: {base_url}")
            
            # Тест 1: Получение списка календарей
            url = f"{base_url}/calendars"
            print_debug(f"GET {url}")
            
            try:
                async with session.get(url, headers=headers, timeout=15) as response:
                    print_debug(f"Статус: {response.status}")
                    print_debug(f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")
                    
                    if response.status == 200:
                        try:
                            data = await response.json()
                            print_success(f"Успешный ответ! Найдено полей: {list(data.keys())}")
                            if 'items' in data:
                                print_success(f"Количество календарей: {len(data['items'])}")
                                for cal in data['items'][:3]:
                                    print_debug(f"  - {cal.get('summary', 'Без названия')} (ID: {cal.get('id', 'unknown')})")
                            elif 'calendars' in data:
                                print_success(f"Количество календарей: {len(data['calendars'])}")
                            else:
                                print_warning(f"Неизвестный формат ответа")
                                print_debug(f"Ответ: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
                        except Exception as e:
                            print_error(f"Ошибка парсинга JSON: {e}")
                            text = await response.text()
                            print_debug(f"Текст ответа: {text[:300]}")
                    
                    elif response.status == 401:
                        print_error("Ошибка 401: Токен недействителен или истек")
                    elif response.status == 403:
                        print_error("Ошибка 403: Недостаточно прав. Проверьте права calendar:read и calendar:write в OAuth-приложении")
                    elif response.status == 404:
                        print_error("Ошибка 404: Эндпоинт не найден")
                    else:
                        print_warning(f"Неожиданный статус: {response.status}")
                        text = await response.text()
                        print_debug(f"Ответ: {text[:300]}")
                        
            except asyncio.TimeoutError:
                print_error("Таймаут подключения")
            except aiohttp.ClientConnectorError as e:
                print_error(f"Ошибка подключения: {e}")
            except Exception as e:
                print_error(f"Неизвестная ошибка: {e}")


async def test_oauth_flow():
    """Тест OAuth flow и получение токена"""
    print_section("3. ТЕСТ OAUTH КОНФИГУРАЦИИ")
    
    print_info(f"CLIENT_ID: {CLIENT_ID[:10]}...{CLIENT_ID[-5:] if CLIENT_ID else 'НЕ ЗАДАН'}")
    print_info(f"CLIENT_SECRET: {'Задан' if CLIENT_SECRET else 'НЕ ЗАДАН'} (длина: {len(CLIENT_SECRET) if CLIENT_SECRET else 0})")
    print_info(f"REDIRECT_URI: {REDIRECT_URI}")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print_error("CLIENT_ID или CLIENT_SECRET не заданы в .env файле!")
        return None
    
    # Формируем URL для авторизации
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI
    }
    auth_url = f"{OAUTH_URL}?{urlencode(params)}"
    
    print_success(f"URL для авторизации сформирован")
    print_info(f"\nДля получения кода авторизации откройте в браузере:\n{auth_url}\n")
    
    # Запрашиваем код у пользователя
    print_warning("Введите код авторизации из адресной строки (после code=):")
    auth_code = input("Код: ").strip()
    
    if not auth_code:
        print_error("Код не введен")
        return None
    
    print_info(f"Получен код: {auth_code[:20]}...")
    
    # Обмениваем код на токен
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(TOKEN_URL, data=data, timeout=15) as response:
                print_debug(f"Статус ответа: {response.status}")
                
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get("access_token")
                    refresh_token = result.get("refresh_token")
                    expires_in = result.get("expires_in")
                    
                    print_success("Токен успешно получен!")
                    print_info(f"Access Token: {access_token[:20]}...{access_token[-10:]}")
                    print_info(f"Refresh Token: {'Есть' if refresh_token else 'Нет'}")
                    print_info(f"Срок действия: {expires_in} секунд ({expires_in//86400} дней)")
                    
                    # Проверяем scope (права доступа)
                    scope = result.get("scope", "")
                    print_info(f"Права доступа (scope): {scope}")
                    
                    if "calendar" not in scope.lower():
                        print_warning("ВНИМАНИЕ: В правах доступа нет calendar! Проверьте настройки OAuth-приложения.")
                    
                    return access_token
                else:
                    error_text = await response.text()
                    print_error(f"Ошибка получения токена: {response.status}")
                    print_debug(f"Ответ: {error_text}")
                    return None
                    
        except Exception as e:
            print_error(f"Исключение при получении токена: {e}")
            return None


async def test_create_event(token: str, calendar_id: str = "primary"):
    """Тест создания тестового события в календаре"""
    print_section("4. ТЕСТ СОЗДАНИЯ СОБЫТИЯ")
    
    if not token:
        print_error("Токен не предоставлен")
        return False
    
    headers = {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json"
    }
    
    # Пробуем разные URL
    base_urls = [
        "https://api.calendar.yandex.net/calendar/v1",
        "https://calendar.yandex.ru/api/v1",
    ]
    
    # Время события: через 1 час, длительность 1 час
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    start_time = now + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    event_data = {
        "summary": "Тестовое событие из диагностического скрипта",
        "description": "Это событие создано для проверки работы API Яндекс Календаря",
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
        for base_url in base_urls:
            url = f"{base_url}/calendars/{calendar_id}/events"
            print_info(f"\nПопытка создать событие через: {url}")
            
            try:
                async with session.post(url, headers=headers, json=event_data, timeout=15) as response:
                    print_debug(f"Статус: {response.status}")
                    
                    if response.status in [200, 201]:
                        data = await response.json()
                        event_id = data.get('id')
                        print_success(f"Событие успешно создано! ID: {event_id}")
                        
                        # Пытаемся удалить тестовое событие
                        print_info("Удаление тестового события...")
                        delete_url = f"{base_url}/calendars/{calendar_id}/events/{event_id}"
                        async with session.delete(delete_url, headers=headers, timeout=15) as del_response:
                            if del_response.status in [200, 204]:
                                print_success("Тестовое событие удалено")
                            else:
                                print_warning(f"Не удалось удалить событие: {del_response.status}")
                        
                        return True
                    else:
                        error_text = await response.text()
                        print_error(f"Ошибка: {response.status}")
                        print_debug(f"Ответ: {error_text[:300]}")
                        
            except Exception as e:
                print_error(f"Ошибка: {e}")
    
    return False


async def load_existing_token():
    """Пытается загрузить существующий токен из файла"""
    token_file = 'user_tokens.json'
    if os.path.exists(token_file):
        try:
            with open(token_file, 'r') as f:
                data = json.load(f)
                # Берем первый токен
                for user_id, token in data.items():
                    print_success(f"Найден существующий токен для пользователя {user_id}")
                    return token
        except:
            pass
    
    # Пробуем из .env
    token = os.getenv('YANDEX_TOKEN')
    if token:
        print_success("Найден токен в .env файле")
        return token
    
    return None


async def main():
    print_section("ДИАГНОСТИКА ЯНДЕКС КАЛЕНДАРЯ")
    print(f"Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. Тест сети
    await test_network_connectivity()
    
    # 2. Проверяем наличие существующего токена
    existing_token = await load_existing_token()
    use_existing = False
    
    if existing_token:
        print_info(f"\nНайден существующий токен: {existing_token[:20]}...")
        answer = input("Использовать существующий токен? (y/n): ").strip().lower()
        use_existing = answer == 'y'
    
    token = None
    if use_existing:
        token = existing_token
        print_success("Используем существующий токен")
    else:
        # 3. Тест OAuth
        token = await test_oauth_flow()
    
    if token:
        # 4. Тест API эндпоинтов с токеном
        await test_api_endpoints(token)
        
        # 5. Тест создания события
        await test_create_event(token)
    else:
        print_warning("\nПропускаем тесты API (нет токена)")
        # Тест API без токена
        await test_api_endpoints()
    
    # Итоги
    print_section("РЕКОМЕНДАЦИИ")
    print_info("""
1. Убедитесь, что в OAuth-приложении на oauth.yandex.ru выбраны права:
   ✅ calendar:read
   ✅ calendar:write
   
2. Проверьте, что в Яндекс Календаре (calendar.yandex.ru) есть хотя бы один календарь.
   Если календаря нет - создайте его и задайте ему цвет.

3. Если API возвращает 404, возможно URL API изменился. Проверьте актуальную документацию:
   https://yandex.ru/dev/calendar/

4. Попробуйте создать новый токен, если старый мог истечь.

5. Убедитесь, что ваше приложение не находится в режиме "Тестирование" (если такая опция есть).
    """)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        traceback.print_exc()