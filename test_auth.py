#!/usr/bin/env python3
"""
Тестовый скрипт для авторизации в Яндексе и получения токена
с правильными правами для Календаря и CalDAV
"""

import asyncio
import json
import os
import webbrowser
from urllib.parse import urlencode, parse_qs, urlparse
import aiohttp
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'https://oauth.yandex.ru/verification_code')
YANDEX_EMAIL = os.getenv('YANDEX_EMAIL', '')

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


def get_auth_url() -> str:
    """Формирует URL для авторизации с ПРАВИЛЬНЫМИ правами"""
    # Важно: указываем ВСЕ необходимые права!
    scopes = [
        "cloud_api:disk.write",      # Яндекс.Диск (запись)
        "cloud_api:disk.read",       # Яндекс.Диск (чтение)
        "cloud_api:disk.info",       # Яндекс.Диск (информация)
        "calendar:read",             # Календарь (чтение)
        "calendar:write",            # Календарь (запись)
        "caldav:read",              # CalDAV (чтение)
        "caldav:write",             # CalDAV (запись)
    ]
    
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(scopes),  # Права через пробел
        "force_confirm": "yes",      # Принудительно показать выбор прав
    }
    
    return f"https://oauth.yandex.ru/authorize?{urlencode(params)}"


async def get_access_token(auth_code: str) -> dict:
    """Обменивает код авторизации на токен"""
    url = "https://oauth.yandex.ru/token"
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=data, timeout=15) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error_text = await response.text()
                    print_error(f"Ошибка получения токена: {response.status}")
                    print_info(f"Ответ: {error_text}")
                    return {}
        except Exception as e:
            print_error(f"Исключение: {e}")
            return {}


async def test_token_permissions(token: str) -> dict:
    """Проверяет, какие права есть у токена"""
    print_header("ПРОВЕРКА ПРАВ ТОКЕНА")
    
    # Проверяем информацию о токене
    url = "https://oauth.yandex.ru/tokeninfo"
    params = {"oauth_token": token}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    print_success("Информация о токене получена:")
                    print_info(f"  Client ID: {data.get('client_id', 'N/A')[:30]}...")
                    
                    scope = data.get('scope', '')
                    if scope:
                        scopes = scope.split()
                        print_info(f"  Права доступа ({len(scopes)}):")
                        for s in scopes:
                            if 'calendar' in s or 'caldav' in s:
                                print_success(f"    ✅ {s}")
                            elif 'disk' in s:
                                print_success(f"    ✅ {s}")
                            else:
                                print_info(f"    • {s}")
                        
                        # Проверяем наличие нужных прав
                        has_calendar_read = any('calendar:read' in s for s in scopes)
                        has_calendar_write = any('calendar:write' in s for s in scopes)
                        has_caldav_read = any('caldav' in s.lower() and 'read' in s.lower() for s in scopes)
                        has_caldav_write = any('caldav' in s.lower() and 'write' in s.lower() for s in scopes)
                        
                        print()
                        if has_calendar_read and has_calendar_write:
                            print_success("✅ Права calendar:read и calendar:write присутствуют")
                        else:
                            print_warning("⚠️ Отсутствуют права calendar:read и/или calendar:write")
                            
                        if has_caldav_read and has_caldav_write:
                            print_success("✅ Права caldav:read и caldav:write присутствуют")
                        else:
                            print_warning("⚠️ Отсутствуют права caldav (могут потребоваться для CalDAV)")
                    
                    expires_in = data.get('expires_in')
                    if expires_in:
                        print_info(f"  Срок действия: {expires_in} секунд ({expires_in // 86400} дней)")
                    
                    return data
                else:
                    print_warning(f"Не удалось получить информацию о токене: {response.status}")
                    return {}
        except Exception as e:
            print_error(f"Ошибка проверки токена: {e}")
            return {}


async def test_disk_access(token: str) -> bool:
    """Проверяет доступ к Яндекс.Диску"""
    print_header("ПРОВЕРКА ДОСТУПА К ЯНДЕКС.ДИСКУ")
    
    headers = {"Authorization": f"OAuth {token}"}
    url = "https://cloud-api.yandex.net/v1/disk"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    print_success(f"✅ Доступ к Диску есть!")
                    print_info(f"  Пользователь: {data.get('user', {}).get('display_name', 'N/A')}")
                    print_info(f"  Использовано: {int(data.get('used_space', 0)) // (1024**3)} ГБ")
                    print_info(f"  Всего: {int(data.get('total_space', 0)) // (1024**3)} ГБ")
                    return True
                else:
                    print_error(f"Ошибка доступа к Диску: {response.status}")
                    return False
        except Exception as e:
            print_error(f"Ошибка: {e}")
            return False


async def test_caldav_access(token: str, email: str) -> bool:
    """Проверяет доступ к CalDAV"""
    print_header("ПРОВЕРКА ДОСТУПА К CalDAV")
    
    import base64
    
    # Basic Auth для CalDAV
    auth_string = f"{email}:{token}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1"
    }
    
    body = '''<?xml version="1.0" encoding="utf-8"?>
<propfind xmlns="DAV:">
  <prop>
    <displayname/>
    <current-user-principal/>
  </prop>
</propfind>'''
    
    url = "https://caldav.yandex.ru/"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request("PROPFIND", url, headers=headers, data=body.encode('utf-8'), timeout=15) as resp:
                if resp.status == 207:
                    print_success("✅ CalDAV доступен! (статус 207)")
                    return True
                elif resp.status == 401:
                    print_error("❌ CalDAV: ошибка авторизации 401")
                    print_info("  Возможные причины:")
                    print_info("  • Токен не имеет прав caldav:read/caldav:write")
                    print_info("  • Email указан неверно")
                    print_info("  • Требуется пароль приложения вместо OAuth-токена")
                    return False
                elif resp.status == 403:
                    print_error("❌ CalDAV: доступ запрещен 403")
                    return False
                else:
                    print_warning(f"⚠️ CalDAV: статус {resp.status}")
                    return False
        except Exception as e:
            print_error(f"❌ Ошибка CalDAV: {e}")
            return False


def save_token_to_env(token: str):
    """Сохраняет токен в .env файл"""
    env_file = '.env'
    
    # Читаем существующий .env
    lines = []
    if os.path.exists(env_file):
        with open(env_file, 'r') as f:
            lines = f.readlines()
    
    # Обновляем или добавляем YANDEX_TOKEN
    token_found = False
    for i, line in enumerate(lines):
        if line.startswith('YANDEX_TOKEN='):
            lines[i] = f'YANDEX_TOKEN={token}\n'
            token_found = True
            break
    
    if not token_found:
        lines.append(f'\nYANDEX_TOKEN={token}\n')
    
    # Сохраняем
    with open(env_file, 'w') as f:
        f.writelines(lines)
    
    print_success(f"✅ Токен сохранен в {env_file}")


def save_token_to_user_tokens(token: str, user_id: str = "admin"):
    """Сохраняет токен в user_tokens.json (формат бота)"""
    token_file = 'user_tokens.json'
    
    data = {}
    if os.path.exists(token_file):
        with open(token_file, 'r') as f:
            data = json.load(f)
    
    data[user_id] = token
    
    with open(token_file, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print_success(f"✅ Токен сохранен в {token_file}")


async def main():
    print_header("АВТОРИЗАЦИЯ В ЯНДЕКСЕ")
    print_info(f"CLIENT_ID: {CLIENT_ID[:20]}..." if CLIENT_ID else "CLIENT_ID не задан!")
    print_info(f"REDIRECT_URI: {REDIRECT_URI}")
    print_info(f"Email для CalDAV: {YANDEX_EMAIL or '(не указан)'}")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print_error("CLIENT_ID или CLIENT_SECRET не заданы в .env!")
        return
    
    # Формируем URL для авторизации
    auth_url = get_auth_url()
    
    print_header("ШАГ 1: ПОЛУЧЕНИЕ КОДА АВТОРИЗАЦИИ")
    print_info("Откройте ссылку в браузере и разрешите доступ:")
    print(f"\n{Colors.BOLD}{auth_url}{Colors.ENDC}\n")
    
    # Пытаемся открыть браузер автоматически
    try:
        webbrowser.open(auth_url)
        print_success("Браузер открыт автоматически!")
    except:
        pass
    
    print_warning("После авторизации вы будете перенаправлены на страницу с кодом.")
    print_info("Скопируйте код из адресной строки (всё после 'code=' до '&' или конца строки)")
    print_info("Пример URL: https://oauth.yandex.ru/verification_code?code=1234567")
    print_info("Нужно скопировать: 1234567")
    
    auth_code = input(f"\n{Colors.BOLD}Введите код авторизации: {Colors.ENDC}").strip()
    
    if not auth_code:
        print_error("Код не введен!")
        return
    
    print_header("ШАГ 2: ПОЛУЧЕНИЕ ТОКЕНА")
    print_info("Обмениваем код на токен...")
    
    token_data = await get_access_token(auth_code)
    
    if not token_data:
        print_error("Не удалось получить токен!")
        return
    
    access_token = token_data.get('access_token')
    refresh_token = token_data.get('refresh_token')
    expires_in = token_data.get('expires_in')
    
    if not access_token:
        print_error("Токен не найден в ответе!")
        return
    
    print_success("Токен успешно получен!")
    print_info(f"Access Token: {access_token[:20]}...{access_token[-10:]}")
    if refresh_token:
        print_info(f"Refresh Token: {refresh_token[:20]}...")
    print_info(f"Срок действия: {expires_in} секунд ({expires_in // 86400} дней)")
    
    # Проверяем права токена
    await test_token_permissions(access_token)
    
    # Проверяем доступ к Диску
    await test_disk_access(access_token)
    
    # Проверяем доступ к CalDAV
    if YANDEX_EMAIL:
        await test_caldav_access(access_token, YANDEX_EMAIL)
    else:
        print_warning("YANDEX_EMAIL не указан, пропускаем проверку CalDAV")
    
    # Сохраняем токен
    print_header("СОХРАНЕНИЕ ТОКЕНА")
    
    save_to_env = input("Сохранить токен в .env файл? (y/n): ").strip().lower()
    if save_to_env == 'y':
        save_token_to_env(access_token)
    
    save_to_json = input("Сохранить токен в user_tokens.json (для бота)? (y/n): ").strip().lower()
    if save_to_json == 'y':
        save_token_to_user_tokens(access_token)
    
    print_header("ГОТОВО!")
    print_success("Авторизация завершена!")
    print_info("Теперь вы можете:")
    print_info("  1. Запустить бота: python MyUved_bot.py")
    print_info("  2. Проверить CalDAV: python test_caldav.py")
    print_info("  3. Проверить календарь в настройках бота")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
    except Exception as e:
        print_error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()