"""Метки ленты: настраиваемые ярлыки на постах для быстрой фильтрации.

Метка — это хэштег. Другого механизма «показать подмножество ленты» у клиента
нет: список — это набор учёток, а не постов; поиск по тексту чужих постов
сервер не даёт; объединение тегов (`any[]`) сервер молча игнорирует —
проверено. Значит одна метка = один хэштег = одна лента по тегу.

Правила решают, какой пост какую метку получает. Метка ставится, если
подходит ХОТЯ БЫ ОДНО её правило. Виды правил:

    account   учётка-автор (`product_radar`, `openhands`) — источник
    tag       на посте уже стоит тег (`контур_приёмка`, или чужой из канала)
    contains  подстрока в тексте без учёта регистра
    regex     регулярное выражение, без учёта регистра — на крайний случай

Файл правил один на всех, кто пишет в ленту: мост, перенос из Telegram,
перемаркировка. Читается и правится плагином dsh. Две копии одной настройки
разъезжаются — поэтому путь один, а не «у каждого свой».

Префикса у меток нет намеренно. Системные теги (`#контур_…`) ведут к
ДЕЙСТВИЮ — чужой пост под ними значит ложное голосование, поэтому они
отделены. Метка — только фильтр: если канал Telegram сам пометил пост
`#маркетинг`, попадание его в срез «Маркетинг» не ошибка, а разметка,
сделанная за нас.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

RULE_KINDS = ("account", "tag", "contains", "regex")



def is_hashtag(value: str) -> bool:
    """Хэштег на сервере: буквы любого алфавита, цифры, подчёркивание; чисто
    цифровой тегом не считается — это уже закреплено тестом в render."""
    return re.fullmatch(r"\w+", value) is not None and not value.isdigit()


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    pattern: re.Pattern | None = None


@dataclass(frozen=True)
class Label:
    id: str
    title: str
    rules: tuple[Rule, ...]


def _rule(raw: dict, label_id: str) -> Rule:
    kinds = [k for k in RULE_KINDS if k in raw]
    if len(kinds) != 1:
        raise ValueError(
            f"метка {label_id!r}: правило должно иметь ровно один вид из "
            f"{RULE_KINDS}, а имеет {kinds or list(raw)}"
        )
    kind = kinds[0]
    value = raw[kind]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"метка {label_id!r}: пустое значение правила {kind!r}")
    value = value.strip()
    pattern = None
    if kind == "regex":
        try:
            pattern = re.compile(value, re.IGNORECASE)
        except re.error as error:
            # Ломаемся при загрузке, а не на первом посте: сломанное правило,
            # найденное в момент публикации, оставило бы пост без метки молча.
            raise ValueError(f"метка {label_id!r}: регулярное выражение не разбирается: {error}") from error
    if kind in ("account", "tag"):
        value = value.lstrip("@#").lower()
    return Rule(kind=kind, value=value, pattern=pattern)


def parse_labels(raw: dict) -> list[Label]:
    items = raw.get("labels")
    if not isinstance(items, list):
        raise ValueError("файл меток: ожидается объект с полем labels (список)")
    out: list[Label] = []
    seen: set[str] = set()
    for item in items:
        label_id = str(item.get("id", "")).strip().lower()
        if not is_hashtag(label_id):
            raise ValueError(
                f"метка {label_id!r}: id обязан быть годным хэштегом — буквы, цифры, "
                "подчёркивание, не только цифры"
            )
        if label_id in seen:
            raise ValueError(f"метка {label_id!r} объявлена дважды")
        seen.add(label_id)
        rules = tuple(_rule(r, label_id) for r in item.get("rules", []))
        out.append(Label(id=label_id, title=str(item.get("title") or label_id), rules=rules))
    return out


def load_labels(path: Path) -> list[Label]:
    """Отсутствующий файл — это «меток нет», а не ошибка: ленту можно вести
    и без них. Но существующий и сломанный файл — ошибка вслух."""
    if not path.exists():
        return []
    return parse_labels(json.loads(path.read_text(encoding="utf-8")))


def _matches(rule: Rule, acct: str, text: str, tags: set[str]) -> bool:
    if rule.kind == "account":
        return acct == rule.value
    if rule.kind == "tag":
        return rule.value in tags
    if rule.kind == "contains":
        return rule.value.lower() in text.lower()
    assert rule.pattern is not None
    return rule.pattern.search(text) is not None


def hashtags_in(text: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"(?<!\w)#(\w+)", text)}


def labels_for(labels: list[Label], acct: str, text: str, tags: set[str] | None = None) -> list[str]:
    """Идентификаторы меток, подошедших посту, в порядке объявления."""
    acct = acct.split("@")[0].lower()
    tags = {t.lower() for t in (tags if tags is not None else hashtags_in(text))}
    return [
        label.id for label in labels
        if any(_matches(rule, acct, text, tags) for rule in label.rules)
    ]


def strip_label_tags(text: str, label_ids: set[str]) -> str:
    """Снимает хэштеги меток из текста — перед тем как поставить актуальные.
    Остальные теги (системные, чужие) не трогает."""
    # Снятый тег уносит с собой пробел перед ним; остальной текст — байт в
    # байт, чтобы перемаркировка не меняла ничего, кроме меток.
    def drop(m: re.Match) -> str:
        return "" if m.group(1).lower() in label_ids else m.group(0)
    return re.sub(r"[ \t]*(?<!\w)#(\w+)", drop, text).rstrip()


def with_labels(labels: list[Label], acct: str, text: str) -> str:
    """Текст поста с дописанными метками. Уже стоящие не дублируются;
    если ни одна не подошла — текст возвращается как есть."""
    wanted = labels_for(labels, acct, text)
    present = hashtags_in(text)
    missing = [l for l in wanted if l not in present]
    if not missing:
        return text
    line = " ".join(f"#{l}" for l in missing)
    # Метки — на той же последней строке, что и системный тег, если он есть.
    text = text.rstrip()
    if hashtags_in(text.rsplit("\n", 1)[-1]):
        return text + " " + line
    return text + "\n\n" + line
