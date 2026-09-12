#!/usr/bin/env python3
"""Переносит собранные заявки из dsh-communication-plugin в ленту.

Единица ленты — ПАЧКА сообщений одного автора в одном чате подряд, склеенных
окном в пять минут. Не отдельное сообщение: человек говорит кусками, и разрезать
высказывание на реплики значит потерять то, что он сказал целиком.

Окно взято не с потолка — это DELAY_BUDGET_SEC коллектора, то же значение,
которым он сам меряет задержку сбора.

Читает базу коллектора ТОЛЬКО на чтение. Ничего в неё не пишет и состояние
разбора не трогает: разбор — решение человека, а перенос в ленту им не является.

Повторный запуск не плодит постов: перенесённые пачки помнятся в отдельном
файле рядом с базой ленты.
"""

import json
import os
import time
import http.client
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Движок меток — общий с мостом, один на всех писателей ленты.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge" / "src"))
from bridge.judge import JudgeError, LlmJudge  # noqa: E402
from bridge.labels import load_labels, needs_judge, with_labels  # noqa: E402

FEED = os.environ.get("FEED_URL", "http://127.0.0.1:8080")
BURST_SECONDS = int(os.environ.get("DELAY_BUDGET_SEC", "300"))
SEEN_FILE = Path(os.environ.get("TG_FEED_SEEN", "telegram-feed-seen.json"))
# Файл меток лежит рядом с базой коллектора — в том же каталоге, который
# читает плагин dsh. Один путь на всех, кто пишет в ленту.
LABELS_FILE = Path(os.environ["LABELS_FILE"]) if os.environ.get("LABELS_FILE") else None

# Чат Telegram -> учётка ленты. Одна учётка на чат: в ленте автором поста
# становится источник, как в Threads автором становится человек.
ACCOUNTS = {
    "Product Radar — лучшие стартапы России": ("product_radar", "Product Radar"),
    "ProblemHunt": ("problemhunt", "ProblemHunt"),
    "100 млн на CRM Harness": ("crm_harness", "100 млн на CRM Harness"),
    "Алексей Ишманов": ("tg_aleks", "Алексей Ишманов"),
}


def api(path, token, data=None, method=None, form=True):
    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data, doseq=True).encode()
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        FEED + path, data=body, headers=headers,
        method=method or ("POST" if data is not None else "GET"),
    )
    # Лента однажды упала под потоком запросов с ошибкой шины и перезапустилась.
    # Обрыв на полпути — не повод терять пачку: повторяем, но не бесконечно,
    # иначе сломанный сервер превратится в вечный цикл.
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"лента не ответила на {method or 'запрос'} {path}: {last}")


def token_of(user):
    """Токены выдаёт scripts/get_tokens.sh — тот же документированный флоу
    authorization_code, уже проверенный живьём. Повторять его здесь значит
    держать вторую реализацию входа, которая разойдётся с первой."""
    name = "FEED_TOKEN_" + user.upper()
    value = os.environ.get(name)
    if not value:
        sys.exit(f"нет {name} — выдайте токен:\n"
                 f"  ./scripts/get_tokens.sh {user}")
    return value


def seconds(value):
    """Коллектор хранит время в миллисекундах, но не будем полагаться на это
    молча: значение больше 1e11 — заведомо миллисекунды."""
    return value / 1000 if value > 1e11 else value


def bursts(rows):
    """Склеивает подряд идущие сообщения одного автора в одном чате."""
    out, cur = [], None
    for r in rows:
        same = (cur and r["chat_title"] == cur["chat_title"]
                and r["author"] == cur["author"]
                and seconds(r["sent_at"]) - seconds(cur["last_at"]) <= BURST_SECONDS)
        if same:
            cur["items"].append(r)
            cur["last_at"] = r["sent_at"]
        else:
            if cur:
                out.append(cur)
            cur = {"chat_title": r["chat_title"], "author": r["author"],
                   "first_at": r["sent_at"], "last_at": r["sent_at"], "items": [r]}
    if cur:
        out.append(cur)
    return out


def render(burst):
    """Текст поста. Пачка склеивается в одно высказывание, ссылки выносятся
    списком: к ним возвращаются отдельно, как и задумано в модели плагина."""
    parts = [i["text"].strip() for i in burst["items"] if i["text"].strip()]
    text = "\n\n".join(parts)
    # Ссылки хранятся строками через перевод строки, не в JSON — см. store.ts
    # плагина: `item.links.join('\n')` при записи и `row.links.split('\n')`
    # при чтении.
    links = []
    for i in burst["items"]:
        for u in (i["links"] or "").split("\n"):
            u = u.strip()
            if u and u not in links:
                links.append(u)
    if links:
        text += "\n\n" + "\n".join(links[:6])
    media = sum(1 for i in burst["items"] if i["has_media"])
    # Исходное время обязано быть в тексте: Mastodon API не даёт публиковать
    # задним числом, и без этой строки лента показывала бы время переноса,
    # выдавая месячную переписку за сегодняшнюю.
    when = datetime.fromtimestamp(seconds(burst["first_at"])).strftime("%d.%m.%Y %H:%M")
    tail = [when]
    if len(burst["items"]) > 1:
        tail.append(f"{len(burst['items'])} сообщения подряд")
    if media:
        tail.append(f"вложений: {media}")
    if any(i["delayed"] for i in burst["items"]):
        tail.append("собрано с задержкой")
    if tail:
        text += "\n\n— " + " · ".join(tail)
    return text[:19000]


def main():
    db = os.environ.get("INBOX_DB")
    if not db or not Path(db).exists():
        sys.exit("нет INBOX_DB или файла по этому пути — укажите базу коллектора")
    seen = set()
    if SEEN_FILE.exists():
        seen = set(json.loads(SEEN_FILE.read_text()))

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "select * from items order by chat_title, sent_at")]
    con.close()

    # Сломанный файл меток роняет перенос ДО первой публикации: иначе посты
    # ушли бы без меток молча, а перемаркировать те, что с вложениями,
    # потом нельзя.
    labels = load_labels(LABELS_FILE) if LABELS_FILE else []
    judge = LlmJudge.from_env(LABELS_FILE.with_name("labels-judgments.json")) if LABELS_FILE else None
    if needs_judge(labels) and judge is None:
        sys.exit(f"метки {needs_judge(labels)} требуют модели (правило prompt): задайте "
                 "LABELS_LLM_BASE_URL, LABELS_LLM_API_KEY, LABELS_LLM_MODEL")

    tokens, published, skipped = {}, 0, 0
    for b in bursts(rows):
        account = ACCOUNTS.get(b["chat_title"])
        if not account:
            skipped += 1
            continue
        user, title = account
        key = f"{b['chat_title']}:{b['items'][0]['key']}"
        if key in seen:
            continue
        if user not in tokens:
            tokens[user] = token_of(user)
            api("/api/v1/accounts/update_credentials", tokens[user],
                {"display_name": title,
                 "note": f"Telegram · {b['chat_title']}\nПеренесено разделом «Управление коммуникацией»"},
                method="PATCH")
        when = datetime.fromtimestamp(seconds(b["first_at"]), tz=timezone.utc)
        try:
            text = with_labels(labels, user, render(b), judge=judge)
        except JudgeError as error:
            # Пост уходит без промт-меток, но вслух: перемаркировка доспросит.
            print(f"  судья отказал, пачка уходит без промт-меток: {error}", file=sys.stderr)
            text = with_labels(labels, user, render(b))
        api("/api/v1/statuses", tokens[user], {
            "status": text,
            "visibility": "public",
            "spoiler_text": "",
            "language": "ru",
        })
        # Журнал пишем СРАЗУ после публикации, а не в конце обхода: при обрыве
        # на середине запись в конце потеряла бы весь прогресс, и повторный
        # запуск задвоил бы уже перенесённое.
        seen.add(key)
        SEEN_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=1))
        published += 1
        # Пауза между публикациями: лента живёт на ноутбуке рядом с браузером
        # и всем остальным, гнать её незачем.
        time.sleep(0.4)
        print(f"  {when.strftime('%d.%m %H:%M')}  @{user:<14} {b['author'] or '—'}")

    print(f"\nперенесено пачек: {published}, пропущено чатов без учётки: {skipped}")


if __name__ == "__main__":
    main()
