from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    """Вариант ответа: что читает человек и какая метка уходит в контур."""

    title: str
    label: str


@dataclass(frozen=True)
class PollPost:
    text: str
    options: list[str]
    expires_in: int | None = None


# Соответствие «фаза задачи -> вопрос и допустимые метки решения».
# Снято с кода контура: набор меток фиксирован пятью значениями, а какие из
# них разбираются, зависит от фазы. Метка не из своей фазы игнорируется
# МОЛЧА, поэтому предлагать можно только то, что фаза действительно ждёт.
QUESTION_BY_PHASE: dict[str, tuple[str, list[Choice]]] = {
    "classified": (
        "Задача классифицирована. Чем её вести дальше?",
        [
            Choice("Разобрать аналитикой", "research-me"),
            Choice("Это баг", "bug-me"),
        ],
    ),
    "ready-for-dev": (
        "Требования готовы. Запускать разработку?",
        [Choice("Запускать разработку", "build-me")],
    ),
    "duplicate": (
        "Задача помечена дубликатом. Так и есть?",
        [
            Choice("Подтверждаю дубликат", "confirm-duplicate"),
            Choice("Не дубликат", "not-duplicate"),
        ],
    ),
}

# Метки, приводящие к отказу контура. Кнопку показываем — прятать правду о
# системе нельзя, — но предупреждаем до нажатия.
NOT_IMPLEMENTED_LABELS = frozenset({"bug-me"})

# Итоговый обзор, правка №6: API ленты (Mastodon-совместимый) требует
# `poll[expires_in]` ОБЯЗАТЕЛЬНЫМ полем при создании опроса — технически
# бессрочного опроса в этой ленте не существует (проверено по
# https://docs.joinmastodon.org/methods/statuses/, живьём не публиковалось).
# У КОНТУРА своего решения по опросу срока нет — по истечении СВОЕГО срока
# ожидания он не применяет вариант, а уводит задачу в эскалацию (это и
# обещает текст поста). У самого ОПРОСА в ленте срок есть — платформа не
# оставляет выбора — и `render_decision` обязан называть его честно, а не
# молчать: раньше `render_decision` не выставлял
# `expires_in`, `Feed.post_poll` тихо подставлял 7 суток от себя, а текст
# поста утверждал «срока нет» — через неделю без ответа развилка молча
# переставала голосоваться.
POLL_LIFETIME_SECONDS = 7 * 24 * 3600


def question_for_phase(phase: str) -> tuple[str, list[Choice]] | None:
    """Вопрос и варианты для фазы. None — фаза решения не ждёт."""
    return QUESTION_BY_PHASE.get(phase)


# Хэштег типа события. На нём держится панель шорткатов: список Mastodon —
# это набор АККАУНТОВ, а «ждут решения» — срез по смыслу, а не по автору.
# Выразить такой срез можно только тегом.
# Префикс обязателен. Пространство хэштегов общее с источниками: перенесённые
# посты Telegram несут собственные теги каналов — на живой ленте это уже
# #мойпродукт, #видеопитчи, #послезапуска. Без префикса чужой пост со словом
# «задача» попал бы в наш срез, и понять почему было бы неоткуда: срез по тегу
# нельзя пересечь со списком источника.
TAG_PREFIX = "контур_"

TAGS = {
    "decision": f"#{TAG_PREFIX}развилка",
    "report": f"#{TAG_PREFIX}приёмка",
    "event": f"#{TAG_PREFIX}задача",
}


def _short_repo(repo: str) -> str:
    return repo.split("/")[-1]


def render_decision(
    repo: str, issue: int, question: str, choices: list[Choice]
) -> PollPost:
    """Развилка. У РЕШЕНИЯ КОНТУРА срока нет намеренно: по истечении своего
    срока ожидания контур НЕ применяет вариант, а снимает задачу с ожидания
    через эскалацию. Обещать дефолт значило бы врать о поведении системы.

    У самого ОПРОСА в ленте срок есть — `POLL_LIFETIME_SECONDS`, платформа
    не оставляет выбора (см. комментарий там же) — и текст поста обязан
    называть его честно, а не заявлять, что срока нет вовсе (итоговый
    обзор, правка №6)."""
    if not choices:
        raise ValueError("развилка без вариантов не имеет смысла")
    lines = [question, ""]
    risky = [c for c in choices if c.label in NOT_IMPLEMENTED_LABELS]
    if risky:
        names = ", ".join(f"«{c.title}»" for c in risky)
        lines.append(f"Осторожно: путь {names} в контуре ещё не реализован — прогон упадёт.")
        lines.append("")
    lines.append("Без ответа контур уводит задачу в эскалацию по своему сроку.")
    lines.append(
        "Сам опрос в ленте открыт 7 суток (требование платформы, не "
        "контура) — если решение придёт позже, переголосовать здесь будет "
        "нельзя."
    )
    lines.append("")
    # Тег типа события идёт последней строкой: он для панели, а не для чтения.
    # Номер задачи тегом не станет — чисто цифровые хэштеги не распознаются.
    lines.append(f"{_short_repo(repo)} · #{issue}  {TAGS['decision']}")
    return PollPost(
        text="\n".join(lines), options=[c.title for c in choices],
        expires_in=POLL_LIFETIME_SECONDS,
    )


def unaccounted_sentences(passed: int, total: int, blocked: list[str]) -> list[str]:
    """Расхождение `total - passed` обязано быть объяснено целиком, а не
    частично. `blocked` — шаги, которые не исполнялись (например, нечем).
    Остаток `total - passed - len(blocked)` — шаги, которые исполнялись и
    ПРОВАЛИЛИСЬ. Это разные вещи: непроверенный шаг и провалившийся шаг
    нельзя называть одним словом, иначе человек решит, что провала не было.
    Если имён провалившихся шагов функции не передали (их и не передают —
    сигнатура несёт только счётчики), об этом говорится словами прямо, а не
    молчанием.

    Общий код для `render_report` и текста продолжения в `publish_report`
    (`bridge.pump`) — расхождение должно объясняться одинаково в обоих
    местах.

    Бросает `ValueError`, если арифметика не сходится
    (`passed + len(blocked) > total`): это значит, что вызывающий передал
    противоречивые числа, и молча принимать их нельзя.
    """
    if passed + len(blocked) > total:
        raise ValueError(
            "арифметика отчёта не сходится: "
            f"passed={passed} + len(blocked)={len(blocked)} больше total={total}"
        )
    sentences = []
    if blocked:
        sentences.append("Не проверял: " + "; ".join(blocked) + ".")
    failed = total - passed - len(blocked)
    if failed > 0:
        sentences.append(f"Провалившихся шагов: {failed} (имена не переданы).")
    return sentences


def render_report(passed: int, total: int, seconds: int, blocked: list[str]) -> str:
    """Отчёт приёмки. Незачтённые шаги обязаны быть названы словами:
    пустая проверка неотличима от пройденной, если о ней промолчать. Сюда
    входят и непроверенные (`blocked`), и провалившиеся — молчание о любой
    из двух категорий запрещено ровно тем же правилом."""
    text = f"Сценарий: {passed} из {total} за {seconds} с.  {TAGS['report']}"
    sentences = unaccounted_sentences(passed, total, blocked)
    if sentences:
        text += "\n\n" + "\n".join(sentences)
    return text


def render_agent_reply(body: str, reason: str) -> str:
    """Ответ агента без причины — это лог, а не сообщение."""
    if not reason.strip():
        raise ValueError("ответ агента обязан нести причину одной строкой")
    return f"{body}\n\nпочему: {reason}"
