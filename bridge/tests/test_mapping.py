from pathlib import Path
from bridge.mapping import Mapping
from bridge.render import Choice

REPO = "po-helper-org/poh-demo-checkout"
CHOICES = [
    Choice(title="Разобрать аналитикой", label="research-me"),
    Choice(title="Это баг", label="bug-me"),
]


def test_thread_remembered_and_found(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    assert m.thread_for(REPO, 149) is None
    m.remember_thread(REPO, 149, "STATUS1")
    assert m.thread_for(REPO, 149) == "STATUS1"
    m.close()


def test_open_polls_lists_only_unclosed(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES)
    m.remember_poll("P2", "S2", REPO, 151, CHOICES)
    m.close_poll("P1")
    left = m.open_polls()
    assert [p.poll_id for p in left] == ["P2"]
    assert left[0].repo == REPO and left[0].issue == 151
    assert left[0].choices == CHOICES
    m.close()


def test_label_for_title_maps_back(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES)
    link = m.open_polls()[0]
    assert link.label_for("Это баг") == "bug-me"
    assert link.label_for("такого варианта нет") is None
    m.close()


def test_seen_is_idempotent(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    assert m.seen("vote:P1") is False
    m.mark_seen("vote:P1")
    assert m.seen("vote:P1") is True
    m.mark_seen("vote:P1")  # повтор не должен падать
    assert m.seen("vote:P1") is True
    m.close()


def test_timings_return_only_decided(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1360.0)
    m.mark_posted("P2", 2000.0)  # не решён — в выборку не идёт
    assert m.timings() == [(1000.0, 1360.0)]
    m.close()


def test_remember_poll_does_not_reopen_closed_poll(tmp_path: Path):
    """Повторный remember_poll по уже закрытому опросу не должен его воскрешать.

    Циклы перекачки (задачи 6-7) обходят ленту многократно и зовут
    remember_poll повторно на тех же данных — закрытый опрос обязан
    оставаться закрытым, иначе один и тот же голос породит второе действие.
    """
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES)
    m.close_poll("P1")
    assert m.open_polls() == []

    m.remember_poll("P1", "S1", REPO, 149, CHOICES)  # повторный обход

    assert m.open_polls() == []
    m.close()


def test_mark_posted_repeat_keeps_decided_at_and_original_posted_at(tmp_path: Path):
    """Повторный mark_posted по тому же опросу не должен стирать decided_at.

    posted_at должен остаться моментом первой публикации развилки, а не
    последнего обхода — иначе замер простоя (главная метрика PoC) молча
    занижается или теряется вовсе.
    """
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1360.0)

    m.mark_posted("P1", 9999.0)  # следующий обход цикла перекачки

    assert m.timings() == [(1000.0, 1360.0)]
    m.close()
