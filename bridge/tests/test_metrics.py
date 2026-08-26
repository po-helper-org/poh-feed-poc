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
    assert "решений: 3" in text
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
    assert "решений: 1" in text
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
