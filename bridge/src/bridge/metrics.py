from statistics import median

from bridge.mapping import Mapping


def idle_minutes(mapping: Mapping) -> list[float]:
    """Простой контура: минуты от публикации развилки до решения человека.

    Значение может выйти отрицательным, если системные часы между
    публикацией и решением сдвинулись назад (перевод времени, коррекция
    NTP). Это не отфильтровывается и не обнуляется: отрицательный простой —
    сигнал об аномалии часов, а не о том, что решение приняли раньше
    вопроса, и прятать его молчаливым клампом значило бы скрыть проблему,
    ради обнаружения которой эта функция и существует.
    """
    return [round((decided - posted) / 60, 1) for posted, decided in mapping.timings()]


def _format_minutes(value: float) -> str:
    """Читаемая длительность. «10080.0 мин» не читается человеком как
    неделя простоя — переходим на часы от часа и на дни от суток.
    Отрицательные значения (см. idle_minutes) форматируются той же логикой
    по модулю величины, знак сохраняется."""
    magnitude = abs(value)
    if magnitude >= 24 * 60:
        return f"{value / (24 * 60):.1f} дн"
    if magnitude >= 60:
        return f"{value / 60:.1f} ч"
    return f"{value:.1f} мин"


def report(mapping: Mapping) -> str:
    """Человекочитаемая сводка простоя — то, ради чего сделан весь PoC:
    падает ли время ожидания решения человека."""
    values = idle_minutes(mapping)
    if not values:
        return "Простой: данных нет — ни одна развилка ещё не решена."
    return (
        f"Простой контура — решений: {len(values)}, "
        f"медиана простоя: {_format_minutes(median(values))}, "
        f"худший случай: {_format_minutes(max(values))}."
    )
