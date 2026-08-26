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
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
    m.remember_poll("P2", "S2", REPO, 151, CHOICES, phase="classified")
    m.close_poll("P1")
    left = m.open_polls()
    assert [p.poll_id for p in left] == ["P2"]
    assert left[0].repo == REPO and left[0].issue == 151
    assert left[0].choices == CHOICES
    m.close()


def test_label_for_title_maps_back(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
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
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
    m.close_poll("P1")
    assert m.open_polls() == []

    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")  # повторный обход

    assert m.open_polls() == []
    m.close()


def test_forget_seen_clears_mark(tmp_path: Path):
    """Правка ревью №2 (задача 7): pump_decisions ставит отметку «сделано» ДО
    публикации и снимает её при отказе — для этого нужен forget_seen."""
    m = Mapping(tmp_path / "m.db")
    m.mark_seen("decision:x:1:classified")
    assert m.seen("decision:x:1:classified") is True

    m.forget_seen("decision:x:1:classified")

    assert m.seen("decision:x:1:classified") is False
    m.mark_seen("decision:x:1:classified")  # можно поставить снова
    assert m.seen("decision:x:1:classified") is True
    m.close()


def test_decided_uncleaned_then_mark_cleaned_stops_returning_it(tmp_path: Path):
    """Правка ревью №4 (задача 7): цель уборки не должна расти без границ —
    once убрано, задача больше не возвращается в decided_uncleaned()."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
    m.close_poll("P1")

    assert m.decided_uncleaned() == [(REPO, 149)]

    m.mark_cleaned(REPO, 149)

    assert m.decided_uncleaned() == []
    m.close()


def test_decided_uncleaned_ignores_still_open_polls(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")  # ещё не закрыт

    assert m.decided_uncleaned() == []
    m.close()


def test_pending_count_counts_rows_without_decision(tmp_path: Path):
    """Правка ревью №1 (задача 10): развилки без решения (decided_at пуст)
    должны быть посчитаны отдельно — иначе отчёт о простое ни словом их не
    упомянет.

    Переименовано в итоговом обзоре (правка №4): имя `undecided_count` и его
    докстринг обещали «увела в эскалацию по таймауту», хотя на деле
    считаются ВСЕ развилки без ответа, включая опубликованную секунду назад
    — отличить одно от другого по имеющимся данным нельзя."""
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1360.0)  # решена — в счёт не идёт
    m.mark_posted("P2", 2000.0)  # без ответа
    m.mark_posted("P3", 3000.0)  # тоже без ответа

    assert m.pending_count() == 2
    m.close()


def test_pending_count_zero_when_all_decided(tmp_path: Path):
    m = Mapping(tmp_path / "m.db")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1360.0)

    assert m.pending_count() == 0
    m.close()


def test_remember_poll_keeps_the_phase_the_poll_was_published_for(tmp_path: Path):
    """Итоговый обзор, правка №2: `pump_votes` должен уметь сверить, что
    задача не уехала в другую фазу, пока опрос висел, — для этого фазу
    обязан помнить сам `PollLink`."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")

    link = m.open_polls()[0]

    assert link.phase == "classified"
    m.close()


def test_mark_cleaned_forgets_the_decision_key_of_that_phase(tmp_path: Path):
    """Итоговый обзор, правка №3: ключ `decision:{repo}:{issue}:{phase}`
    вечен, пока его никто не снимает. Уборка, отработавшая по задаче
    (`mark_cleaned`), обязана снять и его — иначе, вернувшись в ту же фазу,
    задача больше никогда не получит развилку: `pump_decisions` увидит
    ключ «уже виденным» и молча пропустит."""
    m = Mapping(tmp_path / "m.db")
    key = f"decision:{REPO}:149:classified"
    m.mark_seen(key)  # pump_decisions уже опубликовал эту развилку
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
    m.mark_posted("P1", 1000.0)
    m.close_poll("P1")
    m.mark_decided("P1", 1200.0)

    m.mark_cleaned(REPO, 149)

    assert m.seen(key) is False, "ключ развилки этой фазы обязан быть забыт"
    m.close()


def test_mark_cleaned_does_not_touch_a_decision_key_of_a_different_phase(tmp_path: Path):
    """Уборка снимает ключ ИМЕННО той фазы, под которую был опрос — не
    любой ключ развилки этой задачи вообще."""
    m = Mapping(tmp_path / "m.db")
    other_key = f"decision:{REPO}:149:ready-for-dev"
    m.mark_seen(other_key)
    m.remember_poll("P1", "S1", REPO, 149, CHOICES, phase="classified")
    m.close_poll("P1")
    m.mark_posted("P1", 1000.0)
    m.mark_decided("P1", 1200.0)

    m.mark_cleaned(REPO, 149)

    assert m.seen(other_key) is True, "ключ чужой фазы трогать нельзя"
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
