import pytest

from bridge.harness import Parked
from bridge.mapping import Mapping
from bridge.pump import pump_cleanup, pump_decisions, pump_votes
from bridge.render import Choice

REPO = "po-helper-org/poh-demo-checkout"
PARKED = Parked(repo=REPO, issue=149, title="Промокод из ссылки", phase="classified")


class FakeFeed:
    def __init__(self, votes=None):
        self.posts, self.polls, self._votes = [], [], votes or {}

    def post(self, agent, text, *, spoiler=None, in_reply_to=None):
        self.posts.append((agent, text, in_reply_to))
        return f"S{len(self.posts)}"

    def post_poll(self, agent, poll, *, in_reply_to=None):
        self.polls.append((agent, poll, in_reply_to))
        return f"S{len(self.polls)}", f"P{len(self.polls)}"

    def votes(self, poll_id):
        return self._votes.get(poll_id, {})


class FakeGitHub:
    def __init__(self, labels=None):
        self.comments, self.added, self.removed = [], [], []
        self._labels = labels or {}

    def comment(self, repo, issue, body):
        self.comments.append((repo, issue, body))

    def add_label(self, repo, issue, label):
        self.added.append((repo, issue, label))

    def remove_label(self, repo, issue, label):
        self.removed.append((repo, issue, label))

    def labels(self, repo, issue):
        return self._labels.get((repo, issue), [])


class FlakyVotesFeed(FakeFeed):
    """Голоса по одному конкретному опросу недоступны — как сломанная
    задача, из-за которой падает вся лента, в находке №3 ревью."""

    def __init__(self, *a, fail_polls=None, **kw):
        super().__init__(*a, **kw)
        self._fail_polls = fail_polls or set()

    def votes(self, poll_id):
        if poll_id in self._fail_polls:
            raise RuntimeError(f"лента недоступна для {poll_id}")
        return super().votes(poll_id)


class FlakyLabelsGitHub(FakeGitHub):
    """github.labels() отказывает для конкретной задачи — как недоступный
    репозиторий, уронивший контуру 171 обход подряд."""

    def __init__(self, *a, fail_for=None, **kw):
        super().__init__(*a, **kw)
        self._fail_for = fail_for or set()

    def labels(self, repo, issue):
        if (repo, issue) in self._fail_for:
            raise RuntimeError("github: недоступен")
        return super().labels(repo, issue)


class SpyFailFeed(FakeFeed):
    """Фиксирует, стоит ли отметка «сделано» УЖЕ К МОМЕНТУ вызова
    публикации, и симулирует отказ (клиент бросил исключение, не дождавшись
    ответа)."""

    def __init__(self, mapping, key, *a, **kw):
        super().__init__(*a, **kw)
        self._mapping = mapping
        self._key = key
        self.seen_at_call_time = None

    def post_poll(self, agent, poll, *, in_reply_to=None):
        self.seen_at_call_time = self._mapping.seen(self._key)
        raise RuntimeError("таймаут ответа — пост мог физически уйти")


class OnceFailingFeed(FakeFeed):
    """Публикация отказывает РОВНО один раз, затем работает нормально."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._fail = True

    def post_poll(self, agent, poll, *, in_reply_to=None):
        if self._fail:
            self._fail = False
            raise RuntimeError("обрыв связи")
        return super().post_poll(agent, poll, in_reply_to=in_reply_to)


class FlakyMarkSeenMapping:
    """Прокси над настоящим Mapping: mark_seen отказывает N раз подряд —
    книга учёта не устояла ПОСЛЕ того, как реальное действие уже
    состоялось."""

    def __init__(self, real: Mapping, fail_times: int = 0):
        self._real = real
        self._fail_times = fail_times

    def mark_seen(self, key):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("sqlite: диск занят")
        self._real.mark_seen(key)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_parked_issue_becomes_poll_once(tmp_path):
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    assert pump_decisions(feed, m, lambda: [PARKED]) == 1
    assert pump_decisions(feed, m, lambda: [PARKED]) == 0
    assert len(feed.polls) == 1
    agent, poll, _ = feed.polls[0]
    assert agent == "issue_agent"
    assert poll.options == ["Разобрать аналитикой", "Это баг"]
    link = m.open_polls()[0]
    assert link.repo == REPO and link.issue == 149
    assert link.label_for("Разобрать аналитикой") == "research-me"
    m.close()


def test_phase_without_question_is_skipped(tmp_path):
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    working = Parked(repo=REPO, issue=151, title="т", phase="in-development")
    assert pump_decisions(feed, m, lambda: [working]) == 0
    assert feed.polls == []
    m.close()


def test_vote_puts_label_and_comments(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [
        Choice("Разобрать аналитикой", "research-me"), Choice("Это баг", "bug-me"),
    ])
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1, "Это баг": 0}})
    gh = FakeGitHub()
    assert pump_votes(feed, m, gh) == 1
    assert gh.added == [(REPO, 149, "research-me")]
    assert gh.comments == [(
        REPO, 149, "Решение принято через ленту контура: **Разобрать аналитикой**.",
    )]
    assert m.open_polls() == []
    m.close()


def test_label_is_put_once(tmp_path):
    m = Mapping(tmp_path / "m.db")
    choices = [Choice("Разобрать аналитикой", "research-me")]
    m.remember_poll("P1", "S1", REPO, 149, choices)
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1}})
    gh = FakeGitHub()
    pump_votes(feed, m, gh)
    m.remember_poll("P1", "S1", REPO, 149, choices)  # опрос вернулся
    pump_votes(feed, m, gh)
    assert len(gh.added) == 1, "повторная доставка не должна ставить метку дважды"
    m.close()


def test_no_votes_means_no_label(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    feed, gh = FakeFeed(votes={"P1": {"Разобрать аналитикой": 0}}), FakeGitHub()
    assert pump_votes(feed, m, gh) == 0
    assert gh.added == []
    m.close()


def test_decision_timing_is_recorded(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    m.mark_posted("P1", 1000.0)
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1}})
    pump_votes(feed, m, FakeGitHub())
    assert len(m.timings()) == 1
    m.close()


def test_cleanup_removes_stale_decision_label():
    gh = FakeGitHub(labels={(REPO, 149): ["research-me", "phase:business-analysis"]})
    assert pump_cleanup(gh, lambda: [(REPO, 149)]) == 1
    assert gh.removed == [(REPO, 149, "research-me")]


def test_cleanup_keeps_label_while_still_waiting():
    gh = FakeGitHub(labels={(REPO, 149): ["research-me", "needs-human:triage"]})
    assert pump_cleanup(gh, lambda: [(REPO, 149)]) == 0
    assert gh.removed == []


# --- Правки ревью задачи 7 -------------------------------------------------


def test_vote_majority_wins_not_first_option(tmp_path):
    """Находка №1 (критично): раньше брался chosen[0] — первый ненулевой
    вариант ПО ПОРЯДКУ ОПЦИЙ, а не тот, за который отдано больше голосов.
    Воспроизведено ревьюером: 1 голос «Разобрать аналитикой» и 5 «Это баг»
    молча давали метку research-me."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [
        Choice("Разобрать аналитикой", "research-me"), Choice("Это баг", "bug-me"),
    ])
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1, "Это баг": 5}})
    gh = FakeGitHub()

    assert pump_votes(feed, m, gh) == 1

    assert gh.added == [(REPO, 149, "bug-me")], "победить должно большинство, не первая опция"
    m.close()


def test_vote_tie_leaves_poll_open_and_acts_on_nothing(tmp_path):
    """При ничьей решение не наше — не действуем вовсе."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [
        Choice("Разобрать аналитикой", "research-me"), Choice("Это баг", "bug-me"),
    ])
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 3, "Это баг": 3}})
    gh = FakeGitHub()

    assert pump_votes(feed, m, gh) == 0

    assert gh.added == []
    assert gh.comments == []
    assert len(m.open_polls()) == 1, "опрос при ничьей остаётся открытым"
    m.close()


def test_vote_tie_is_named_in_problems(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [
        Choice("Разобрать аналитикой", "research-me"), Choice("Это баг", "bug-me"),
    ])
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 3, "Это баг": 3}})
    problems: list[tuple[int, str]] = []

    pump_votes(feed, m, FakeGitHub(), problems)

    assert [number for number, _ in problems] == [149]
    m.close()


def test_comment_failure_does_not_lose_already_recorded_label(tmp_path):
    """Находка №2 (критично): комментарий может отказать УЖЕ ПОСЛЕ того, как
    решение состоялось — метка поставлена. Комментарий — действие «по
    возможности», его отказ не должен ни отменять решение, ни ронять весь
    вызов."""
    class FailCommentGitHub(FakeGitHub):
        def comment(self, repo, issue, body):
            raise RuntimeError("сеть оборвалась")

    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1}})
    gh = FailCommentGitHub()

    assert pump_votes(feed, m, gh) == 1
    assert gh.added == [(REPO, 149, "research-me")]
    assert m.open_polls() == [], "решение принято и опрос закрыт, несмотря на отказ комментария"
    m.close()


def test_comment_not_duplicated_when_bookkeeping_fails_right_after_it(tmp_path):
    """Находка №2 (критично): раньше mark_seen ставился ПОСЛЕДНИМ. Если
    что-то в книге учёта падало ПОСЛЕ того, как комментарий уже реально
    ушёл, повторный обход слал комментарий ЗАНОВО. Теперь отметка «сделано»
    и закрытие опроса идут СРАЗУ за меткой, ДО комментария — второй заход
    этот опрос уже не видит вовсе."""
    real = Mapping(tmp_path / "m.db")
    choices = [Choice("Разобрать аналитикой", "research-me")]
    real.remember_poll("P1", "S1", REPO, 149, choices)
    m = FlakyMarkSeenMapping(real, fail_times=1)
    feed = FakeFeed(votes={"P1": {"Разобрать аналитикой": 1}})
    gh = FakeGitHub()

    pump_votes(feed, m, gh)  # первый обход: книга учёта не устояла — тихо ловится
    pump_votes(feed, m, gh)  # второй обход того же цикла перекачки

    assert len(gh.comments) == 1, "комментарий не должен дублироваться"
    real.close()


def test_broken_poll_does_not_block_the_rest(tmp_path):
    """Находка №3 (критично): у pump_votes не было try/except вокруг тела
    цикла — сломанный опрос топил обработку здоровых. Воспроизведено
    ревьюером как повтор беды контура (171 падение подряд)."""
    m = Mapping(tmp_path / "m.db")
    choices = [Choice("Разобрать аналитикой", "research-me")]
    m.remember_poll("P1", "S1", REPO, 149, choices)
    m.remember_poll("P2", "S2", REPO, 151, choices)
    feed = FlakyVotesFeed(votes={"P2": {"Разобрать аналитикой": 1}}, fail_polls={"P1"})
    gh = FakeGitHub()
    problems: list[tuple[int, str]] = []

    assert pump_votes(feed, m, gh, problems) == 1

    assert gh.added == [(REPO, 151, "research-me")], "здоровый опрос обязан обработаться"
    assert [number for number, _ in problems] == [149]
    m.close()


def test_seen_mark_is_set_before_publish_and_rolled_back_on_failure(tmp_path):
    """Находка №2 (критично): отметка «сделано» обязана стоять УЖЕ к
    моменту вызова публикации (иначе отказ после физической отправки не
    оставляет следа), а при отказе публикации — сниматься, чтобы развилка
    не была потеряна навсегда."""
    m = Mapping(tmp_path / "m.db")
    key = f"decision:{PARKED.repo}:{PARKED.issue}:{PARKED.phase}"
    feed = SpyFailFeed(m, key)

    with pytest.raises(RuntimeError):
        pump_decisions(feed, m, lambda: [PARKED])

    assert feed.seen_at_call_time is True, "отметка обязана стоять ДО публикации"
    assert m.seen(key) is False, "после отказа отметка обязана быть снята"
    m.close()


def test_retry_after_publish_failure_succeeds_without_losing_the_fork(tmp_path):
    m = Mapping(tmp_path / "m.db")
    feed = OnceFailingFeed()

    with pytest.raises(RuntimeError):
        pump_decisions(feed, m, lambda: [PARKED])
    assert feed.polls == []

    assert pump_decisions(feed, m, lambda: [PARKED]) == 1
    assert len(feed.polls) == 1
    m.close()


def test_cleanup_broken_issue_does_not_block_the_rest():
    """Находка №3 (критично): то же требование поштучного try/except для
    pump_cleanup."""
    gh = FlakyLabelsGitHub(
        labels={(REPO, 151): ["research-me", "phase:x"]}, fail_for={(REPO, 149)},
    )
    problems: list[tuple[int, str]] = []

    assert pump_cleanup(gh, lambda: [(REPO, 149), (REPO, 151)], problems=problems) == 1

    assert gh.removed == [(REPO, 151, "research-me")]
    assert [number for number, _ in problems] == [149]


def test_pump_cleanup_marks_issue_cleaned_so_it_is_not_revisited(tmp_path):
    """Находка №4 (важно): once убрано — не возвращаться, иначе мост
    навсегда дёргал бы GitHub по каждой когда-либо решённой задаче."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    m.close_poll("P1")
    gh = FakeGitHub(labels={(REPO, 149): ["research-me", "phase:x"]})

    touched = m.decided_uncleaned()
    assert touched == [(REPO, 149)]
    assert pump_cleanup(gh, lambda: touched, m.mark_cleaned) == 1

    assert m.decided_uncleaned() == [], "убранная задача не должна возвращаться в обход"
    m.close()


def test_pump_cleanup_marks_cleaned_even_when_nothing_to_remove(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    m.close_poll("P1")
    gh = FakeGitHub(labels={(REPO, 149): ["phase:x"]})  # снимать нечего

    assert pump_cleanup(gh, lambda: m.decided_uncleaned(), m.mark_cleaned) == 0

    assert m.decided_uncleaned() == []
    m.close()
