#!/usr/bin/env python3
"""Перемаркировка: приводит метки на уже опубликованных постах к правилам.

Метка — хэштег в тексте поста, поставить её чужому посту нельзя. Но чужих
постов в ленте нет: каждый написан нашей учёткой — источником или агентом, —
и токены этих учёток у нас есть. Поэтому «переразметить ленту» значит
отредактировать свои посты (`PUT /api/v1/statuses/{id}`), сервер перечитает
теги, и пост появится в ленте новой метки.

По умолчанию — только показывает, что изменилось бы. `--apply` правит.

Что НЕ трогается, и это не осторожность, а проверенный факт:

  - посты с ОПРОСОМ: правка без повтора параметров опроса убивает опрос
    (ответ 200, опроса нет); с повтором — опрос пересоздаётся с новым id,
    и мост, помнящий старый, перестаёт видеть голоса;
  - посты с ВЛОЖЕНИЯМИ: та же семантика правки — не пересланные media_ids
    отваливаются.

Такие посты получают метки только при публикации (см. `Feed._labeled`).
Здесь они перечисляются отдельной строкой, чтобы их не считали размеченными.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge" / "src"))
from bridge.judge import JudgeError, LlmJudge  # noqa: E402
from bridge.labels import labels_for, load_labels, needs_judge, strip_label_tags  # noqa: E402

FEED = os.environ.get("FEED_URL", "http://127.0.0.1:8080")
HUMAN = os.environ.get("FEED_HUMAN", "aleks")


def call(path, token, data=None, method=None):
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(
        FEED + path, data=body, headers={"Authorization": f"Bearer {token}"},
        method=method or ("POST" if data is not None else "GET"),
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def plain(html: str) -> str:
    """Текст поста для правил. Сервер отдаёт HTML; теги вырезаются, переводы
    строк из <br> и </p> сохраняются, чтобы contains/regex видели то же,
    что человек."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))


def own_accounts() -> dict[str, str]:
    """Учётки, чьи посты можно править: все с токеном в окружении, кроме
    человека — его реплики не размечаются автоматикой."""
    out = {}
    for name, token in os.environ.items():
        if name.startswith("FEED_TOKEN_") and token:
            user = name[len("FEED_TOKEN_"):].lower()
            if user != HUMAN:
                out[user] = token
    return out


def statuses_of(token: str, account_id: str, limit: int):
    max_id = None
    got = 0
    while got < limit:
        page = call(
            f"/api/v1/accounts/{account_id}/statuses?limit=40&exclude_reblogs=true"
            + (f"&max_id={max_id}" if max_id else ""), token,
        )
        if not page:
            return
        for status in page:
            yield status
            got += 1
            if got >= limit:
                return
        max_id = page[-1]["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="править посты, а не только показывать")
    ap.add_argument("--limit", type=int, default=200, help="постов на учётку (по умолчанию 200)")
    args = ap.parse_args()

    labels_file = os.environ.get("LABELS_FILE")
    if not labels_file:
        sys.exit("нет LABELS_FILE — укажите путь к файлу меток (тот же, что у плагина dsh)")
    labels = load_labels(Path(labels_file))
    if not labels:
        sys.exit(f"в {labels_file} нет ни одной метки — нечего ставить")
    label_ids = {label.id for label in labels}
    judge = LlmJudge.from_env(Path(labels_file).with_name("labels-judgments.json"))
    if needs_judge(labels) and judge is None:
        sys.exit(f"метки {needs_judge(labels)} требуют модели (правило prompt): задайте "
                 "LABELS_LLM_BASE_URL, LABELS_LLM_API_KEY, LABELS_LLM_MODEL")
    accounts = own_accounts()
    if not accounts:
        sys.exit("нет ни одного FEED_TOKEN_* в окружении — править нечем")

    print(f"меток: {len(labels)} ({', '.join(l.id for l in labels)}); учёток: {len(accounts)}")
    print("режим:", "ПРАВКА" if args.apply else "только показ (добавьте --apply)")
    print()

    per_label: dict[str, int] = {l.id: 0 for l in labels}
    to_edit, untouchable, unchanged, failed = [], [], 0, []
    for user, token in sorted(accounts.items()):
        try:
            me = call("/api/v1/accounts/verify_credentials", token)
        except urllib.error.HTTPError as error:
            failed.append((user, f"токен не принят: {error.code}"))
            continue
        for status in statuses_of(token, me["id"], args.limit):
            text = plain(status["content"])
            current_tags = {t["name"].lower() for t in status.get("tags", [])}
            try:
                wanted = labels_for(labels, user, text, current_tags, judge=judge)
            except JudgeError as error:
                failed.append((f"{user} {status['id']}", f"судья: {error}"))
                continue
            for l in wanted:
                per_label[l] += 1
            have = current_tags & label_ids
            if set(wanted) == have:
                unchanged += 1
                continue
            if status.get("poll") or status.get("media_attachments"):
                untouchable.append((user, status["id"], sorted(set(wanted) - have)))
                continue
            to_edit.append((user, token, status, wanted))

    print("постов по меткам (все посты учёток, включая уже размеченные):")
    for l in labels:
        print(f"  {l.title:<14} #{l.id:<14} {per_label[l.id]}")
    print(f"\nбез изменений: {unchanged}; править: {len(to_edit)}; "
          f"с опросом/вложением — нельзя: {len(untouchable)}"
          + (f"; вопросов модели: {judge.asked}" if judge else ""))
    for user, sid, missing in untouchable[:8]:
        print(f"  не тронут @{user} {sid}: не хватает {missing}")
    if len(untouchable) > 8:
        print(f"  … и ещё {len(untouchable) - 8}")

    if not args.apply:
        for user, _, status, wanted in to_edit[:12]:
            head = plain(status["content"]).split("\n")[0][:60]
            print(f"  @{user:<14} {status['id']}  → {wanted}   «{head}»")
        if len(to_edit) > 12:
            print(f"  … и ещё {len(to_edit) - 12}")
        return 0

    edited = 0
    for user, token, status, wanted in to_edit:
        try:
            source = call(f"/api/v1/statuses/{status['id']}/source", token)["text"]
            body = strip_label_tags(source, label_ids)
            if wanted:
                line = " ".join(f"#{l}" for l in wanted)
                last = body.rsplit("\n", 1)[-1]
                body = body + (" " if "#" in last else "\n\n") + line
            payload = {"status": body}
            if status.get("spoiler_text"):
                payload["spoiler_text"] = status["spoiler_text"]
            call(f"/api/v1/statuses/{status['id']}", token, payload, method="PUT")
            edited += 1
            time.sleep(0.3)
        except Exception as error:  # каждый отказ назван, обход продолжается
            failed.append((f"{user} {status['id']}", str(error)[:120]))
    print(f"\nотредактировано: {edited}")
    for where, why in failed:
        print(f"  ОТКАЗ {where}: {why}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
