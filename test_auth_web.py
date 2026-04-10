#!/usr/bin/env python3
"""
Тест авторизации для bothost.ru (без интерактивного ввода)
Работает с существующим токеном из .env или user_tokens.json
"""

import asyncio
import json
import os
import base64
import aiohttp
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
YANDEX_TOKEN = os.getenv('YANDEX_TOKEN')
YANDEX_EMAIL = os.getenv('YANDEX_EMAIL', '')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Цветной вывод
class Colors:
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    OKCYAN = '\033[96m'
    OKBLUE = '\033[94m'
    BOLD = '\033[1m'
    ENDC = '\033[0m'

def print_success(msg): print(f"{Colors.OKGREEN}✅ {msg}{Colors.ENDC}")
def print_error(msg): print(f"{Colors.FAIL}❌ {msg}{Colors.ENDC}")
def print_warning(msg): print(f"{Colors.WARNING}⚠️ {msg}{Colors.ENDC}")
def print_info(msg): print(f"{Colors.OKCYAN}ℹ️ {msg}{Colors.ENDC}")
def print_header(msg): 
    print(f"\n{Colors.BOLD}{Colors.OKBLUE}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKBLUE} {msg}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKBLUE}{'='*60}{Colors.ENDC}")


def load_token_from_sources() -> tuple:
    """Загружает токен из всех возможных источников"""
    token = None
    source = None
    
    # 1. Из переменной окружения
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
    
    # 3. Из config.json
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
    return None, None


async def test_token_info(token: str):
    """Проверяет информацию о токене"""
    print_header("ИНФОРМАЦИЯ О ТОКЕНЕ")
    
    print_info(f"Токен: {token[:20]}...{token[-10:] if len(token) > 30 else ''}")
    print_info(f"Длина: {len(token)} символов")
    
    # Проверяем формат
    if token.startswith('y0_') or token.startswith('y1_'):
        print_success("Формат: новый (y0_/y1_)")
    elif token.startswith('AQAAAA'):
        print_success("Формат: старый (AQAAAA)")
    else:
        print_warning(f"Неизвестный формат: {token[:10]}...")
    
    # Получаем информацию о токене
    url = "https://oauth.yandex.ru/tokeninfo"
    params = {"oauth_token": token}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print_success("Информация о токене:")
                    print_info(f"  Client ID: {data.get('client_id', 'N/A')[:30]}...")
                    
                    scope = data.get('scope', '')
                    if scope:
                        scopes = scope.split()
                        print_info(f"  Права доступа ({len(scopes)}):")
                        
                        has_calendar = False
                        has_caldav = False
                        has_disk = False
                        
                        for s in scopes:
                            if 'calendar:read' in s:
                                print_success(f"    ✅ {s}")
                                has_calendar = True
                            elif 'calendar:write' in s:
                                print_success(f"    ✅ {s}")
                                has_calendar = True
                            elif 'caldav' in s.lower():
                                print_success(f"    ✅ {s}")
                                has_caldav = True
                            elif 'disk' in s:
                                print_success(f"    ✅ {s}")
                                has_disk = True
                            else:
                                print_info(f"    • {s}")
                        
                        print()
                        if has_calendar:
                            print_success("✅ Права для Календаря (calendar:read/write) есть")
                        else:
                            print_error("❌ НЕТ прав для Календаря!")
                            
                        if has_caldav:
                            print_success("✅ Права для CalDAV есть")
                        else:
                            print_warning("⚠️ Нет прав CalDAV (могут потребоваться)")
                            
                        if has_disk:
                            print_success("✅ Права для Диска есть")
                        else:
                            print_warning("⚠️ Нет прав для Диска")
                    
                    return data
                else:
                    print_error(f"Ошибка получения информации: {resp.status}")
                    return None
        except Exception as e:
            print_error(f"Ошибка: {e}")
            return None


async def test_disk_access(token: str):
    """Проверяет доступ к Яндекс.Диску"""
    print_header("ПРОВЕРКА ЯНДЕКС.ДИСКА")
    
    headers = {"Authorization": f"OAuth {token}"}
    url = "https://cloud-api.yandex.net/v1/disk"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print_success("✅ Диск доступен!")
                    print_info(f"  Пользователь: {data.get('user', {}).get('display_name', 'N/A')}")
                    used = int(data.get('used_space', 0))
                    total = int(data.get('total_space', 0))
                    print_info(f"  Использовано: {used // (1024**3)} ГБ из {total // (1024**3)} ГБ")
                    return True
                else:
                    print_error(f"❌ Ошибка доступа: {resp.status}")
                    return False
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return False


async def test_caldav_access(token: str, email: str):
    """Проверяет доступ к CalDAV"""
    print_header("ПРОВЕРКА CalDAV")
    
    if not email:
        print_warning("Email не указан, пропускаем проверку CalDAV")
        return False
    
    print_info(f"Email: {email}")
    
    # Basic Auth
    auth_string = f"{email}:{token}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1"
    }
    
    body = '''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <prop>
    <displayname/>
    <resourcetype/>
  </prop>
</propfind>'''
    
    url = "https://caldav.yandex.ru/"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request("PROPFIND", url, headers=headers, data=body.encode('utf-8'), timeout=15) as resp:
                print_info(f"Статус: {resp.status}")
                
                if resp.status == 207:
                    print_success("✅ CalDAV работает!")
                    
                    # Парсим календари
                    text = await resp.text()
                    import xml.etree.ElementTree as ET
                    
                    try:
                        root = ET.fromstring(text)
                        namespaces = {'D': 'DAV:', 'C': 'urn:ietf:params:xml:ns:caldav'}
                        
                        calendars = []
                        for response in root.findall('.//D:response', namespaces):
                            href = response.find('.//D:href', namespaces)
                            displayname = response.find('.//D:displayname', namespaces)
                            resourcetype = response.find('.//D:resourcetype', namespaces)
                            
                            if resourcetype is not None:
                                calendar_tag = resourcetype.find('.//C:calendar', namespaces)
                                if calendar_tag is not None and href is not None:
                                    calendars.append({
                                        'path': href.text,
                                        'name': displayname.text if displayname is not None else 'Без названия'
                                    })
                        
                        if calendars:
                            print_success(f"Найдено календарей: {len(calendars)}")
                            for cal in calendars:
                                print_info(f"  📅 {cal['name']}: {cal['path']}")
                        else:
                            print_warning("Календари не найдены")
                    except Exception as e:
                        print_warning(f"Ошибка парсинга: {e}")
                    
                    return True
                    
                elif resp.status == 401:
                    print_error("❌ Ошибка авторизации 401")
                    print_info("  Причины:")
                    print_info("  • Токен не имеет прав caldav")
                    print_info("  • Email указан неверно")
                    print_info("  • Требуется пароль приложения вместо OAuth-токена")
                    return False
                    
                elif resp.status == 403:
                    print_error("❌ Доступ запрещен 403")
                    return False
                    
                else:
                    print_error(f"❌ Статус: {resp.status}")
                    return False
                    
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return False


async def test_bot_token():
    """Проверяет токен бота"""
    print_header("ПРОВЕРКА ТОКЕНА БОТА")
    
    if not BOT_TOKEN:
        print_error("BOT_TOKEN не найден в .env")
        return False
    
    print_info(f"BOT_TOKEN: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('ok'):
                        bot_info = data.get('result', {})
                        print_success("✅ Токен бота действителен!")
                        print_info(f"  Имя бота: @{bot_info.get('username', 'N/A')}")
                        print_info(f"  ID бота: {bot_info.get('id', 'N/A')}")
                        return True
                    else:
                        print_error("❌ Токен бота недействителен")
                        return False
                else:
                    print_error(f"❌ Ошибка: {resp.status}")
                    return False
        except Exception as e:
            print_error(f"❌ Ошибка: {e}")
            return False


def generate_auth_url() -> str:
    """Генерирует URL для ручной авторизации"""
    from urllib.parse import urlencode
    
    scopes = [
        "cloud_api:disk.write",
        "cloud_api:disk.read",
        "cloud_api:disk.info",
        "calendar:read",
        "calendar:write",
    ]
    
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": "https://oauth.yandex.ru/verification_code",
        "scope": " ".join(scopes),
        "force_confirm": "yes",
    }
    
    return f"https://oauth.yandex.ru/authorize?{urlencode(params)}"


async def main():
    print_header("ДИАГНОСТИКА АВТОРИЗАЦИИ (bothost.ru)")
    print_info(f"Время: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Проверяем конфигурацию
    print_header("КОНФИГУРАЦИЯ")
    print_info(f"CLIENT_ID: {'Задан' if CLIENT_ID else 'НЕ ЗАДАН'}")
    print_info(f"CLIENT_SECRET: {'Задан' if CLIENT_SECRET else 'НЕ ЗАДАН'}")
    print_info(f"YANDEX_EMAIL: {YANDEX_EMAIL or 'НЕ ЗАДАН'}")
    print_info(f"BOT_TOKEN: {'Задан' if BOT_TOKEN else 'НЕ ЗАДАН'}")
    
    # Проверяем токен бота
    await test_bot_token()
    
    # Загружаем токен Яндекса
    token, source = load_token_from_sources()
    
    if not token:
        print_header("ПОЛУЧЕНИЕ НОВОГО ТОКЕНА")
        print_warning("Токен не найден. Необходимо получить новый токен.")
        print_info("1. Откройте ссылку в браузере:")
        print(f"\n{Colors.BOLD}{generate_auth_url()}{Colors.ENDC}\n")
        print_info("2. Разрешите доступ")
        print_info("3. Скопируйте код из адресной строки")
        print_info("4. Добавьте в .env файл: YANDEX_TOKEN=полученный_код")
        print_info("   (или используйте кнопку «Авторизация» в боте)")
        return
    
    # Проверяем токен
    await test_token_info(token)
    
    # Проверяем Диск
    await test_disk_access(token)
    
    # Проверяем CalDAV
    await test_caldav_access(token, YANDEX_EMAIL)
    
    # Итоги
    print_header("ИТОГИ")
    print_info("Если CalDAV не работает:")
    print_info("  1. Убедитесь, что в OAuth-приложении есть права calendar:read и calendar:write")
    print_info("  2. Попробуйте использовать пароль приложения вместо OAuth-токена")
    print_info("  3. Пароль приложения можно создать: https://id.yandex.ru/security/app-passwords")
    
    # Сохраняем результаты
    results = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "token_source": source,
        "token_valid": True,
        "email": YANDEX_EMAIL,
    }
    
    with open('auth_test_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print_success("\n📁 Результаты сохранены в auth_test_results.json")


if __name__ == "__main__":
    asyncio.run(main())