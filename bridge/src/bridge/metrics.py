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
    по модулю величины, знак сохраняется.

    Граница проверяется не только по самой величине, но и по тому, во что
    её округлит `:.1f` — иначе 1439.9 минуты (0.1 мин не хватает до суток)
    печатались бы как «24.0 ч», что у порога суток читается нелепо.
    Симметрично для часовой границы: «60.0 мин» вместо «1.0 ч»."""
    magnitude = abs(value)
    if magnitude >= 24 * 60 or round(magnitude / 60, 1) >= 24.0:
        return f"{value / (24 * 60):.1f} дн"
    if magnitude >= 60 or round(magnitude, 1) >= 60.0:
        return f"{value / 60:.1f} ч"
    return f"{value:.1f} мин"


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование числительного со словом: 1 решение,
    2 решения, 5 решений, 21 решение, 25 решений."""
    n_abs = abs(n) % 100
    last = n_abs % 10
    if 11 <= n_abs <= 14:
        return many
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def _decisions_phrase(n: int) -> str:
    return f"{n} {_plural_ru(n, 'решение', 'решения', 'решений')}"


def _undecided_phrase(n: int) -> str:
    return f"{n} {_plural_ru(n, 'развилка', 'развилки', 'развилок')}"


def report(mapping: Mapping) -> str:
    """Человекочитаемая сводка простоя — то, ради чего сделан весь PoC:
    падает ли время ожидания решения человека.

    Развилки, которые система увела в эскалацию по таймауту, так и не
    дождавшись ответа человека, не попадают в idle_minutes() (там нечего
    измерять — decided_at пуст), но обязаны быть названы в этой же
    строке: иначе читающий один раз в день увидит красивую медиану по
    быстрым решениям и не узнает, что половина развилок осталась без
    ответа — а это ровно то молчание, против которого строится весь
    инструмент."""
    values = idle_minutes(mapping)
    undecided = mapping.undecided_count()
    if not values and not undecided:
        return "Простой: данных нет — ни одна развилка ещё не решена."
    if not values:
        return (
            f"Простой контура: решений пока нет, "
            f"без ответа: {_undecided_phrase(undecided)}."
        )
    return (
        f"Простой контура — {_decisions_phrase(len(values))} "
        f"(медиана простоя: {_format_minutes(median(values))}, "
        f"худший случай: {_format_minutes(max(values))}), "
        f"без ответа: {_undecided_phrase(undecided)}."
    )
