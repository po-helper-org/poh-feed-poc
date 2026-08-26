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
