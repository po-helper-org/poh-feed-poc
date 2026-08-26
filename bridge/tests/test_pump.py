import pytest

from bridge.harness import Parked
from bridge.mapping import Mapping
from bridge.pump import pump_cleanup, pump_decisions, pump_github, pump_votes
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


class FlakyPostPollFeed(FakeFeed):
    """post_poll отказывает для конкретной задачи — как сломанная развилка,
    из-за которой раньше падал весь цикл `pump_decisions` (находка №1
    повторного ревью, симметрично `FlakyVotesFeed` для pump_votes)."""

    def __init__(self, *a, fail_issues=None, **kw):
        super().__init__(*a, **kw)
        self._fail_issues = fail_issues or set()

    def post_poll(self, agent, poll, *, in_reply_to=None):
        if any(f"#{issue}" in poll.text for issue in self._fail_issues):
            raise RuntimeError("обрыв связи")
        return super().post_poll(agent, poll, in_reply_to=in_reply_to)


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


class FakeGitHubIssues(FakeGitHub):
    def __init__(self, issues):
        super().__init__()
        self._issues = issues

    def recent_issues(self, repo, limit=20):
        return self._issues


class FlakyPostFeed(FakeFeed):
    """feed.post отказывает для конкретного номера задачи — как сломанное
    событие GitHub, из-за которого раньше падал бы весь обход (симметрично
    `FlakyPostPollFeed` для `pump_decisions`)."""

    def __init__(self, *a, fail_numbers=None, **kw):
        super().__init__(*a, **kw)
        self._fail_numbers = fail_numbers or set()

    def post(self, agent, text, *, spoiler=None, in_reply_to=None):
        if any(f"#{n}" in text for n in self._fail_numbers):
            raise RuntimeError("обрыв связи")
        return super().post(agent, text, spoiler=spoiler, in_reply_to=in_reply_to)


class FailingRepoGitHub(FakeGitHub):
    """recent_issues отказывает для конкретного репозитория целиком — как
    недоступный репозиторий, уронивший контуру 171 обход подряд."""

    def __init__(self, per_repo, fail_repos=None):
        super().__init__()
        self._per_repo = per_repo
        self._fail_repos = fail_repos or set()

    def recent_issues(self, repo, limit=20):
        if repo in self._fail_repos:
            raise RuntimeError(f"github: репозиторий {repo} недоступен")
        return self._per_repo.get(repo, [])


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
    """Находка №2 (критично, первый круг правок): отметка «сделано» обязана
    стоять УЖЕ к моменту вызова публикации (иначе отказ после физической
    отправки не оставляет следа), а при отказе публикации — сниматься, чтобы
    развилка не была потеряна навсегда.

    Контракт изменился в повторном ревью (находка №1): pump_decisions больше
    не поднимает исключение наверх, а ловит его поштучно и пишет причину в
    `problems` — симметрично pump_votes/pump_cleanup. Раньше тест проверял
    `pytest.raises(RuntimeError)`; теперь то же самое видно по `problems`."""
    m = Mapping(tmp_path / "m.db")
    key = f"decision:{PARKED.repo}:{PARKED.issue}:{PARKED.phase}"
    feed = SpyFailFeed(m, key)
    problems: list[tuple[int, str]] = []

    made = pump_decisions(feed, m, lambda: [PARKED], problems)

    assert made == 0
    assert feed.seen_at_call_time is True, "отметка обязана стоять ДО публикации"
    assert m.seen(key) is False, "после отказа отметка обязана быть снята"
    assert [number for number, _ in problems] == [PARKED.issue]
    m.close()


def test_retry_after_publish_failure_succeeds_without_losing_the_fork(tmp_path):
    m = Mapping(tmp_path / "m.db")
    feed = OnceFailingFeed()
    problems: list[tuple[int, str]] = []

    assert pump_decisions(feed, m, lambda: [PARKED], problems) == 0
    assert feed.polls == []
    assert len(problems) == 1

    assert pump_decisions(feed, m, lambda: [PARKED]) == 1
    assert len(feed.polls) == 1
    m.close()


def test_broken_decision_does_not_block_the_rest(tmp_path):
    """Находка №1 повторного ревью (критично): у pump_decisions не было
    поштучного try/except вокруг тела цикла — `forget_seen` и `raise`
    прерывали весь `for`, и ни одна развилка после сломанной в этом заходе
    не обрабатывалась. Ревьюер воспроизвёл это как две развилки, где
    сломанная первой топит здоровую вторую.

    Заодно проверяет находку №2 (мелочь): счётчик `made` обязан отражать
    реально опубликованное, а не теряться при отказе на середине списка —
    после исправления находки №1 это следует само собой из того, что `made`
    инкрементируется только на успешных итерациях внутри продолжающегося
    цикла."""
    m = Mapping(tmp_path / "m.db")
    broken = Parked(repo=REPO, issue=149, title="Промокод из ссылки", phase="classified")
    healthy = Parked(repo=REPO, issue=151, title="Другая задача", phase="classified")
    feed = FlakyPostPollFeed(fail_issues={149})
    problems: list[tuple[int, str]] = []

    made = pump_decisions(feed, m, lambda: [broken, healthy], problems)

    assert made == 1, "здоровая развилка обязана быть опубликована и учтена"
    assert len(feed.polls) == 1
    _, poll, _ = feed.polls[0]
    assert "#151" in poll.text, "опубликована обязана быть именно здоровая развилка"
    assert [number for number, _ in problems] == [149]
    key_broken = f"decision:{REPO}:149:classified"
    assert m.seen(key_broken) is False, "отметка сломанной развилки обязана быть снята"
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


def test_vote_for_unknown_option_does_not_close_poll_and_is_named_in_problems(tmp_path):
    """Находка №3 повторного ревью (важно): раньше ветка, где
    `link.label_for(title)` вернул None (голос за заголовок, которого нет
    среди вариантов опроса), закрывала опрос и шла дальше — решение
    терялось молча, без следа. Теперь опрос остаётся открытым, причина
    уходит в problems с номером задачи и полученным заголовком."""
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [
        Choice("Разобрать аналитикой", "research-me"), Choice("Это баг", "bug-me"),
    ])
    feed = FakeFeed(votes={"P1": {"Опечатка в варианте": 1}})
    gh = FakeGitHub()
    problems: list[tuple[int, str]] = []

    assert pump_votes(feed, m, gh, problems) == 0

    assert gh.added == []
    assert len(m.open_polls()) == 1, "опрос обязан остаться открытым — человек проголосовал"
    assert [number for number, _ in problems] == [149]
    assert "Опечатка в варианте" in problems[0][1], (
        "причина обязана называть полученный заголовок"
    )
    m.close()


def test_pump_cleanup_marks_cleaned_even_when_nothing_to_remove(tmp_path):
    m = Mapping(tmp_path / "m.db")
    m.remember_poll("P1", "S1", REPO, 149, [Choice("Разобрать аналитикой", "research-me")])
    m.close_poll("P1")
    gh = FakeGitHub(labels={(REPO, 149): ["phase:x"]})  # снимать нечего

    assert pump_cleanup(gh, lambda: m.decided_uncleaned(), m.mark_cleaned) == 0

    assert m.decided_uncleaned() == []
    m.close()


# --- Задача 8: события GitHub в ветки --------------------------------------


def test_new_issue_opens_thread(tmp_path):
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    gh = FakeGitHubIssues([
        {"number": 149, "title": "Промокод из ссылки", "state": "open",
         "updated": "2026-08-25T09:14:00"},
    ])
    n = pump_github(feed, m, gh, ["po-helper-org/poh-demo-checkout"])
    assert n == 1
    assert "Промокод из ссылки" in feed.posts[0][1]
    assert m.thread_for("po-helper-org/poh-demo-checkout", 149) is not None
    m.close()


def test_same_update_is_not_reposted(tmp_path):
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    issues = [{"number": 149, "title": "Промокод", "state": "open",
               "updated": "2026-08-25T09:14:00"}]
    gh = FakeGitHubIssues(issues)
    pump_github(feed, m, gh, ["po-helper-org/poh-demo-checkout"])
    assert pump_github(feed, m, gh, ["po-helper-org/poh-demo-checkout"]) == 0
    assert len(feed.posts) == 1
    m.close()


def test_closed_issue_replies_into_existing_thread(tmp_path):
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    m.remember_thread("po-helper-org/poh-demo-checkout", 149, "S9")
    gh = FakeGitHubIssues([
        {"number": 149, "title": "Промокод", "state": "closed",
         "updated": "2026-08-25T14:02:00"},
    ])
    pump_github(feed, m, gh, ["po-helper-org/poh-demo-checkout"])
    assert feed.posts[0][2] == "S9", "ответ обязан уйти в существующую ветку"
    m.close()


def test_state_change_reopens_a_new_event_not_the_same_key(tmp_path):
    """Разное время обновления — разные ключи идемпотентности: закрытие
    issue после того, как ветка уже открыта, обязано дать новый пост-ответ,
    а не быть молча проглочено как «уже видели»."""
    feed, m = FakeFeed(), Mapping(tmp_path / "m.db")
    gh_open = FakeGitHubIssues([
        {"number": 149, "title": "Промокод", "state": "open",
         "updated": "2026-08-25T09:14:00"},
    ])
    assert pump_github(feed, m, gh_open, [REPO]) == 1

    gh_closed = FakeGitHubIssues([
        {"number": 149, "title": "Промокод", "state": "closed",
         "updated": "2026-08-25T14:02:00"},
    ])
    assert pump_github(feed, m, gh_closed, [REPO]) == 1
    assert len(feed.posts) == 2
    assert feed.posts[1][2] == m.thread_for(REPO, 149), "ответ обязан уйти в ту же ветку"
    m.close()


def test_broken_issue_publish_does_not_block_the_rest_and_is_named_in_problems(tmp_path):
    """Находка задачи 7, применённая симметрично к `pump_github`: отказ по
    одной задаче не должен останавливать разбор остальных (беда контура —
    171 падение подряд из-за одного недоступного элемента)."""
    feed = FlakyPostFeed(fail_numbers={149})
    m = Mapping(tmp_path / "m.db")
    gh = FakeGitHubIssues([
        {"number": 149, "title": "Сломанная", "state": "open",
         "updated": "2026-08-25T09:14:00"},
        {"number": 151, "title": "Здоровая", "state": "open",
         "updated": "2026-08-25T09:15:00"},
    ])
    problems: list[tuple[int, str]] = []

    made = pump_github(feed, m, gh, [REPO], problems)

    assert made == 1, "здоровое событие обязано быть опубликовано и учтено"
    assert "Здоровая" in feed.posts[0][1]
    assert [number for number, _ in problems] == [149]
    m.close()


def test_seen_mark_is_set_before_publish_and_rolled_back_on_github_failure(tmp_path):
    """Отметка «сделано» обязана стоять уже к моменту публикации (иначе
    отказ после физической отправки не оставляет следа) и сниматься при
    отказе — иначе событие потеряно навсегда. Симметрично тесту для
    `pump_decisions`."""
    feed = FlakyPostFeed(fail_numbers={149})
    m = Mapping(tmp_path / "m.db")
    gh = FakeGitHubIssues([
        {"number": 149, "title": "Сломанная", "state": "open",
         "updated": "2026-08-25T09:14:00"},
    ])
    key = "gh:po-helper-org/poh-demo-checkout:149:2026-08-25T09:14:00"
    problems: list[tuple[int, str]] = []

    made = pump_github(feed, m, gh, [REPO], problems)

    assert made == 0
    assert m.seen(key) is False, "после отказа отметка обязана быть снята"
    assert [number for number, _ in problems] == [149]
    m.close()


def test_retry_after_publish_failure_succeeds_without_losing_the_event(tmp_path):
    m = Mapping(tmp_path / "m.db")
    gh = FakeGitHubIssues([
        {"number": 149, "title": "Промокод", "state": "open",
         "updated": "2026-08-25T09:14:00"},
    ])
    feed = FlakyPostFeed(fail_numbers={149})

    assert pump_github(feed, m, gh, [REPO]) == 0
    assert feed.posts == []

    feed2 = FakeFeed()
    assert pump_github(feed2, m, gh, [REPO]) == 1
    assert len(feed2.posts) == 1
    m.close()


def test_unreadable_repo_does_not_silently_drop_events_it_raises(tmp_path):
    """Отказ `github.recent_issues(repo)` целиком (репозиторий недоступен) не
    ловится внутри `pump_github` — он уходит наверх, как и у
    `GitHub.parked()` для `pump_decisions`; `cli.py` перехватывает его на
    уровне обхода и печатает причину. Проверяем, что это не тихое
    поглощение: событие не помечается как обработанное."""
    m = Mapping(tmp_path / "m.db")
    gh = FailingRepoGitHub(
        per_repo={REPO: [
            {"number": 149, "title": "Промокод", "state": "open",
             "updated": "2026-08-25T09:14:00"},
        ]},
        fail_repos={"another-org/another-repo"},
    )
    feed = FakeFeed()

    with pytest.raises(RuntimeError):
        pump_github(feed, m, gh, ["another-org/another-repo", REPO])

    assert feed.posts == [], "порядок репозиториев: сломанный обработан первым, здоровый не достигнут"
    m.close()
