#!/usr/bin/env python3
"""Тест CalDAV подключения к Яндекс Календарю"""
import asyncio
import base64
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import aiohttp
from dotenv import load_dotenv

load_dotenv()

async def test_caldav():
    token = os.getenv('YANDEX_TOKEN')
    email = os.getenv('YANDEX_EMAIL', '')
    
    print("=" * 60)
    print("ТЕСТ CalDAV ПОДКЛЮЧЕНИЯ К ЯНДЕКС КАЛЕНДАРЮ")
    print("=" * 60)
    
    if not token:
        print("❌ YANDEX_TOKEN не найден в .env")
        print("Добавьте в .env: YANDEX_TOKEN=ваш_токен")
        return
    
    print(f"📧 Email: {email or '(не указан)'}")
    print(f"🔑 Token: {token[:15]}...{token[-10:] if len(token) > 30 else ''}")
    
    # Basic Auth для CalDAV
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
    print(f"\n🔍 PROPFIND {url}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request("PROPFIND", url, headers=headers, data=body.encode('utf-8'), timeout=15) as resp:
                print(f"Статус: {resp.status}")
                
                if resp.status == 207:
                    print("✅ CalDAV работает! Статус 207 Multi-Status")
                    text = await resp.text()
                    
                    # Парсим календари
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
                            print(f"✅ Найдено календарей: {len(calendars)}")
                            for cal in calendars:
                                print(f"  📅 {cal['name']}: {cal['path']}")
                        else:
                            print("⚠️ Календари не найдены в ответе")
                            print("Возможно, у вас нет календарей. Создайте календарь на calendar.yandex.ru")
                    except Exception as e:
                        print(f"❌ Ошибка парсинга XML: {e}")
                        
                elif resp.status == 401:
                    print("❌ Ошибка авторизации 401")
                    print("   Проверьте, что:")
                    print("   1. Токен действителен")
                    print("   2. Email указан верно")
                    print("   3. В OAuth-приложении есть права calendar:read и calendar:write")
                elif resp.status == 403:
                    print("❌ Доступ запрещен 403")
                else:
                    print(f"⚠️ Неожиданный статус: {resp.status}")
                    text = await resp.text()
                    if 'html' in text.lower():
                        print("❌ Сервер вернул HTML вместо XML. Это не CalDAV ответ!")
                    print(f"Ответ (первые 300 символов): {text[:300]}")
                    
        except aiohttp.ClientConnectorError as e:
            print(f"❌ Ошибка подключения: {e}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_caldav())