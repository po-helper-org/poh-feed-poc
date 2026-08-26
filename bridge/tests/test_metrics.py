import pytest

from bridge.mapping import Mapping
from bridge.metrics import idle_minutes, report


def test_idle_minutes_counts_only_decided(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1360.0)   # 6 минут
    m.mark_posted("P2", 2000.0)    # не решён — в счёт не идёт
    assert idle_minutes(m) == [6.0]
    m.close()


def test_report_says_nothing_when_no_data(tmp_path):
    m = Mapping(tmp_path / "m.db")
    assert "данных нет" in report(m)
    m.close()


def test_report_shows_median_and_count(tmp_path):
    m = Mapping(tmp_path / "m.db")
    for i, (posted, decided) in enumerate([(0, 60), (0, 300), (0, 600)]):
        m.mark_posted(f"P{i}", float(posted))
        m.mark_decided(f"P{i}", float(decided))
    text = report(m)
    assert "3 решения" in text
    assert "медиана простоя: 5.0 мин" in text
    assert "худший случай: 10.0 мин" in text
    m.close()


def test_report_handles_single_decision(tmp_path):
    """Медиана от одного значения — само значение, не деление на ноль
    и не исключение statistics.StatisticsError."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)
    m.mark_decided("P1", 120.0)  # 2 минуты
    text = report(m)
    assert "1 решение" in text
    assert "медиана простоя: 2.0 мин" in text
    assert "худший случай: 2.0 мин" in text
    m.close()


def test_report_formats_long_idle_in_readable_units(tmp_path):
    """«10080.0 мин» никто не прочитает как неделю — большие значения
    печатаются в часах или днях."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)
    m.mark_decided("P1", 7 * 24 * 3600.0)  # ровно неделя простоя
    text = report(m)
    assert "7.0 дн" in text
    assert "мин" not in text
    m.close()


def test_idle_minutes_allows_negative_when_clock_moves_backward(tmp_path):
    """Часы могли уйти назад между публикацией и решением (перевод
    времени, коррекция NTP) — считаем и показываем как есть, не прячем
    аномалию молчаливым обнулением."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 700.0)  # решение "раньше" публикации по часам
    assert idle_minutes(m) == [-5.0]
    text = report(m)
    assert "-5.0 мин" in text
    m.close()


# Правка ревью №1 (задача 10): report() врал умолчанием — развилки, не
# дошедшие до решения (уведённые в эскалацию по таймауту), в выборку
# timings() не попадают вовсе, и отчёт печатал красивую медиану, ни словом
# не упоминая, что часть развилок осталась без ответа.


def test_report_names_undecided_alongside_decided(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)
    m.mark_decided("P1", 60.0)  # решена
    m.mark_posted("P2", 100.0)  # эскалирована по таймауту, ответа нет
    m.mark_posted("P3", 200.0)  # тоже без ответа

    text = report(m)

    assert "1 решение" in text
    assert "2" in text and "без ответа" in text
    m.close()


def test_report_names_undecided_when_none_are_decided(tmp_path):
    """Решённых нет вовсе, но есть неотвеченные — «данных нет» здесь
    неправда: развилки есть, просто ни на одну не ответили."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)  # ждёт решения человека
    m.mark_posted("P2", 100.0)  # тоже ждёт

    text = report(m)

    assert "данных нет" not in text
    assert "без ответа" in text
    assert "2" in text
    m.close()


def test_report_names_zero_undecided_when_all_decided(tmp_path):
    """Число нерешённых обязано быть в строке всегда — и когда оно ноль,
    иначе один-единственный раз, когда оно есть, легко потерять фразу."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)
    m.mark_decided("P1", 60.0)

    text = report(m)

    assert "без ответа" in text
    assert "0" in text
    m.close()


# Правка ревью №2 (задача 10): у самого порога суток «24.0 ч» читается
# нелепо — граница должна переходить в дни раньше, чем округление до
# часов даёт «24.0».


def test_report_formats_near_day_boundary_as_days_not_24_hours(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 0.0)
    m.mark_decided("P1", 1439.9 * 60)  # 1439.9 минуты — почти сутки

    text = report(m)

    assert "1.0 дн" in text
    assert "24.0 ч" not in text
    m.close()


# Правка ревью №3 (задача 10): «решений: 1» — родительный падеж
# множественного при единице читается неестественно; числительное должно
# согласовываться со словом при 1, 2, 5, 21.


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, "1 решение"),
        (2, "2 решения"),
        (5, "5 решений"),
        (21, "21 решение"),
    ],
)
def test_report_agrees_numeral_with_word_for_decisions(tmp_path, count, expected):
    m = Mapping(tmp_path / "m.db")
    for i in range(count):
        m.mark_posted(f"P{i}", float(i))
        m.mark_decided(f"P{i}", float(i) + 60.0)

    text = report(m)

    assert expected in text
    m.close()


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, "1 развилка"),
        (2, "2 развилки"),
        (5, "5 развилок"),
        (21, "21 развилка"),
    ],
)
def test_report_agrees_numeral_with_word_for_undecided(tmp_path, count, expected):
    m = Mapping(tmp_path / "m.db")
    for i in range(count):
        m.mark_posted(f"P{i}", float(i))  # все без ответа

    text = report(m)

    assert expected in text
    m.close()
