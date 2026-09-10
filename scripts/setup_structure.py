#!/usr/bin/env python3
"""Собирает структуру ленты: списки по источникам и подписку на теги.

Три оси ленты — источник, пользователь, лейбл — ложатся на четыре механизма,
и одной оси механизм не нужен:

  пользователь -> учётка          уже 1:1
  источник     -> СПИСОК          источник это свойство учётки: каждая
                                  принадлежит ровно одному источнику
  лейбл        -> ХЭШТЕГ          срез по смыслу, списком не выразить
  лейбл наотрез-> фильтр          что вычесть из ленты (здесь не настраивается)

Подписка на тег вливает его в ДОМАШНЮЮ ленту. Это и снимает напряжение
«лейблов много, а мест в панели девять»: тег, по которому есть действие, не
занимает места в панели — он просто приходит домой.

Правило подписки: тег подписывается, только если по нему человек ДЕЙСТВУЕТ.
Описательные лейблы (проект, тема, продукт) не подписываются никогда — иначе
домашняя лента превратится в ту же кашу, из которой уходим.

Запускается повторно без вреда: существующие списки не пересоздаются,
повторная подписка на тег не ломается.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

FEED = os.environ.get("FEED_URL", "http://127.0.0.1:8080")

# Один список на источник — решение владельца. Дробить внутри источника
# (каналы отдельно от групповых чатов) не стали: это вторая ось, а она уже
# выражена учётками.
SOURCES = {
    "Telegram": ["product_radar", "problemhunt", "crm_harness", "tg_aleks"],
    "Контур": ["issue_agent", "openhands", "pr_agent", "howtodemo", "delivery", "harness"],
    # Источники объявлены, учёток пока нет. Список создаётся пустым намеренно:
    # пустой список честно говорит «источник заявлен, постов нет», а его
    # отсутствие не говорит ничего.
    "Email": [],
    "MTS.Link": [],
    "AI-Hermes": [],
}

# Подписка только на действенные теги. Решение владельца: развилка и приёмка.
FOLLOW_TAGS = ["контур_развилка", "контур_приёмка"]


def call(path, token, data=None, method=None):
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(
        FEED + path, data=body, headers={"Authorization": f"Bearer {token}"},
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:120]


def main():
    token = os.environ.get("FEED_TOKEN_ALEKS")
    if not token:
        sys.exit("нет FEED_TOKEN_ALEKS — выдайте токен человека:\n"
                 "  ./scripts/get_tokens.sh aleks")

    print("== Списки по источникам ==")
    _, existing = call("/api/v1/lists", token)
    by_title = {item["title"]: item["id"] for item in existing} if isinstance(existing, list) else {}

    for title, users in SOURCES.items():
        if title in by_title:
            list_id = by_title[title]
            state = "уже есть"
        else:
            code, created = call("/api/v1/lists", token, {"title": title})
            if code != 200:
                print(f"  {title:<12} НЕ СОЗДАН: {created}")
                continue
            list_id, state = created["id"], "создан"

        added = 0
        for user in users:
            code, account = call(f"/api/v1/accounts/lookup?acct={user}", token)
            if code != 200:
                print(f"    учётки {user} нет")
                continue
            # Список принимает только тех, на кого подписан человек.
            code, _ = call(f"/api/v1/lists/{list_id}/accounts", token,
                           {"account_ids[]": account["id"]})
            if code == 200:
                added += 1
        note = f"{len(users)} учёток" if users else "пуст — источник заявлен, постов нет"
        print(f"  {title:<12} {state}, {note}")

    print("\n== Подписка на теги ==")
    for tag in FOLLOW_TAGS:
        code, _ = call(f"/api/v1/tags/{urllib.parse.quote(tag)}/follow", token, {})
        print(f"  #{tag:<20} {'подписан' if code == 200 else f'отказ {code}'}")

    code, followed = call("/api/v1/followed_tags", token)
    if isinstance(followed, list):
        print(f"\nВсего подписок на теги: {[t['name'] for t in followed]}")

    # Проверяем делом: структура, которая создалась, но ничего не показывает,
    # выглядит точно так же, как настроенная правильно.
    print("\n== Проверка ==")
    _, lists = call("/api/v1/lists", token)
    for item in lists if isinstance(lists, list) else []:
        _, posts = call(f"/api/v1/timelines/list/{item['id']}?limit=20", token)
        count = len(posts) if isinstance(posts, list) else 0
        print(f"  список «{item['title']}»: {count} постов")


if __name__ == "__main__":
    main()
