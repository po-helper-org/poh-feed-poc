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


def question_for_phase(phase: str) -> tuple[str, list[Choice]] | None:
    """Вопрос и варианты для фазы. None — фаза решения не ждёт."""
    return QUESTION_BY_PHASE.get(phase)


def _short_repo(repo: str) -> str:
    return repo.split("/")[-1]


def render_decision(
    repo: str, issue: int, question: str, choices: list[Choice]
) -> PollPost:
    """Развилка. Срока у опроса нет намеренно: срок ожидания держит контур,
    и по его истечении он НЕ применяет вариант, а снимает задачу с ожидания
    через эскалацию. Обещать дефолт значило бы врать о поведении системы."""
    if not choices:
        raise ValueError("развилка без вариантов не имеет смысла")
    lines = [question, ""]
    risky = [c for c in choices if c.label in NOT_IMPLEMENTED_LABELS]
    if risky:
        names = ", ".join(f"«{c.title}»" for c in risky)
        lines.append(f"Осторожно: путь {names} в контуре ещё не реализован — прогон упадёт.")
        lines.append("")
    lines.append("Без ответа контур уводит задачу в эскалацию по своему сроку.")
    lines.append("")
    lines.append(f"{_short_repo(repo)} · #{issue}")
    return PollPost(text="\n".join(lines), options=[c.title for c in choices])


def render_artifact(title: str, words: int, body_md: str) -> tuple[str, str]:
    """Фрагмент: свёртка видна всем, документ разворачивается нажатием."""
    return f"{title} · {words} слов", body_md


def render_report(passed: int, total: int, seconds: int, blocked: list[str]) -> str:
    """Отчёт приёмки. Незачтённые шаги обязаны быть названы словами:
    пустая проверка неотличима от пройденной, если о ней промолчать."""
    text = f"Сценарий: {passed} из {total} за {seconds} с."
    if blocked:
        text += "\n\nне проверял: " + "; ".join(blocked) + "."
    return text


def render_incident(what: str, evidence: str) -> str:
    return f"{what}\n\n{evidence}"


def render_agent_reply(body: str, reason: str) -> str:
    """Ответ агента без причины — это лог, а не сообщение."""
    if not reason.strip():
        raise ValueError("ответ агента обязан нести причину одной строкой")
    return f"{body}\n\nпочему: {reason}"
