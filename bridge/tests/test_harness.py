from datetime import datetime

import pytest

from bridge.harness import (
    NEEDS_HUMAN,
    GitHub,
    Parked,
    is_parked,
    parked_from,
    phase_of,
    stale_decision_labels,
)


def test_phase_read_from_label():
    assert phase_of(["phase:classified", "priority:P3"]) == "classified"


def test_phase_absent_gives_none():
    assert phase_of(["priority:P3"]) is None


def test_parked_detected_by_single_label():
    assert is_parked(["needs-human:triage", "phase:classified"]) is True
    assert is_parked(["phase:classified"]) is False


def test_parked_from_keeps_only_waiting_issues():
    issues = [
        {"repo": "o/r", "number": 149, "title": "Промокод",
         "labels": ["needs-human:triage", "phase:classified"]},
        {"repo": "o/r", "number": 151, "title": "Другое",
         "labels": ["phase:in-development"]},
    ]
    assert parked_from(issues) == [
        Parked(repo="o/r", issue=149, title="Промокод", phase="classified")
    ]


def test_parked_without_phase_label_is_skipped():
    """Без фазы неизвестно, какие метки решения допустимы, а метка не из своей
    фазы игнорируется контуром МОЛЧА. Лучше не предлагать ничего."""
    issues = [{"repo": "o/r", "number": 7, "title": "т",
               "labels": ["needs-human:triage"]}]
    assert parked_from(issues) == []


def test_stale_decision_labels_only_when_no_longer_waiting():
    assert stale_decision_labels(["research-me", "phase:business-analysis"]) == ["research-me"]
    assert stale_decision_labels(["research-me", "needs-human:triage"]) == []
    assert stale_decision_labels(["phase:merged"]) == []


def test_stale_decision_labels_returns_all_of_them():
    got = stale_decision_labels(["research-me", "build-me", "phase:merged"])
    assert sorted(got) == ["build-me", "research-me"]


# --- Правка 1: неоднозначная фаза называется явно, а не угадывается --------


def test_phase_of_raises_on_multiple_phase_labels():
    with pytest.raises(ValueError, match="несколько"):
        phase_of(["phase:classified", "phase:merged"])


def test_phase_of_error_names_issue_and_found_labels():
    with pytest.raises(ValueError) as excinfo:
        phase_of(["phase:classified", "phase:merged"], issue=149)
    message = str(excinfo.value)
    assert "149" in message
    assert "classified" in message and "merged" in message


def test_parked_from_skips_ambiguous_issue_but_parses_the_rest():
    """Одна сломанная задача не должна мешать разобрать соседние — это прямой
    урок системы, которую мы обслуживаем (один недоступный репозиторий уронил
    весь обход и дал 171 падение подряд)."""
    issues = [
        {"repo": "o/r", "number": 149, "title": "сломана",
         "labels": ["needs-human:triage", "phase:classified", "phase:merged"]},
        {"repo": "o/r", "number": 150, "title": "нормальная",
         "labels": ["needs-human:triage", "phase:in-development"]},
    ]
    assert parked_from(issues) == [
        Parked(repo="o/r", issue=150, title="нормальная", phase="in-development")
    ]


def test_parked_from_reports_ambiguous_phase_in_problems():
    issues = [
        {"repo": "o/r", "number": 149, "title": "сломана",
         "labels": ["needs-human:triage", "phase:classified", "phase:merged"]},
    ]
    problems: list[tuple[int, str]] = []
    assert parked_from(issues, problems) == []
    assert len(problems) == 1
    number, reason = problems[0]
    assert number == 149
    assert "classified" in reason and "merged" in reason


def test_parked_from_reports_missing_phase_in_problems():
    issues = [{"repo": "o/r", "number": 7, "title": "т",
               "labels": ["needs-human:triage"]}]
    problems: list[tuple[int, str]] = []
    assert parked_from(issues, problems) == []
    assert problems == [(7, "нет метки фазы")]


def test_parked_from_without_problems_list_does_not_raise():
    """Если problems не передан, поведение прежнее — молча пропускает."""
    issues = [
        {"repo": "o/r", "number": 149, "title": "сломана",
         "labels": ["needs-human:triage", "phase:classified", "phase:merged"]},
    ]
    assert parked_from(issues) == []


# --- Подделка клиента PyGithub ----------------------------------------------


class FakeLabel:
    def __init__(self, name: str):
        self.name = name


class FakeIssue:
    def __init__(self, number, title, labels, state="open", updated_at=None):
        self.number = number
        self.title = title
        self.labels = [FakeLabel(name) for name in labels]
        self.state = state
        self.updated_at = updated_at or datetime(2026, 1, 1)
        self.comments: list[str] = []
        self.added_labels: list[str] = []
        self.removed_labels: list[str] = []

    def create_comment(self, body):
        self.comments.append(body)

    def add_to_labels(self, label):
        self.added_labels.append(label)

    def remove_from_labels(self, label):
        self.removed_labels.append(label)


class FakeRepo:
    def __init__(self, issues):
        self._issues = {i.number: i for i in issues}
        self.calls: list[dict] = []

    def get_issue(self, number):
        return self._issues[number]

    def get_issues(self, **kw):
        self.calls.append(kw)
        result = list(self._issues.values())
        state = kw.get("state", "open")
        if state != "all":
            result = [i for i in result if i.state == state]
        labels = kw.get("labels")
        if labels:
            result = [
                i for i in result
                if all(name in [lb.name for lb in i.labels] for name in labels)
            ]
        if kw.get("sort") == "updated":
            result = sorted(result, key=lambda i: i.updated_at, reverse=True)
        return result


class FakeGithubClient:
    def __init__(self, repos: dict):
        self._repos = repos
        self.requested_repos: list[str] = []

    def get_repo(self, name):
        self.requested_repos.append(name)
        return self._repos[name]


@pytest.fixture
def fake_repo():
    return FakeRepo([
        FakeIssue(149, "Промокод", ["needs-human:triage", "phase:classified"],
                   updated_at=datetime(2026, 1, 3)),
        FakeIssue(150, "Другое", ["phase:in-development"],
                   updated_at=datetime(2026, 1, 2)),
        FakeIssue(151, "Третье", ["needs-human:triage", "phase:merged"],
                   updated_at=datetime(2026, 1, 1)),
    ])


def test_github_labels_converts_to_list_of_strings(fake_repo):
    gh = GitHub("token", client=FakeGithubClient({"o/r": fake_repo}))
    assert gh.labels("o/r", 149) == ["needs-human:triage", "phase:classified"]


def test_github_parked_filters_by_needs_human_and_returns_parked(fake_repo):
    client = FakeGithubClient({"o/r": fake_repo})
    gh = GitHub("token", client=client)

    result = gh.parked(["o/r"])

    assert result == [
        Parked(repo="o/r", issue=149, title="Промокод", phase="classified"),
        Parked(repo="o/r", issue=151, title="Третье", phase="merged"),
    ]
    # факт передачи фильтра, а не поведение подделки
    assert fake_repo.calls[-1] == {"state": "open", "labels": [NEEDS_HUMAN]}
    assert client.requested_repos == ["o/r"]


def test_github_parked_forwards_problems_from_parked_from():
    broken = FakeRepo([
        FakeIssue(200, "битая",
                   ["needs-human:triage", "phase:classified", "phase:merged"]),
    ])
    gh = GitHub("token", client=FakeGithubClient({"o/r": broken}))
    problems: list[tuple[int, str]] = []

    assert gh.parked(["o/r"], problems) == []

    assert len(problems) == 1
    number, reason = problems[0]
    assert number == 200
    assert "classified" in reason and "merged" in reason


def test_github_recent_issues_respects_limit_and_dict_shape(fake_repo):
    gh = GitHub("token", client=FakeGithubClient({"o/r": fake_repo}))

    out = gh.recent_issues("o/r", limit=2)

    assert len(out) == 2
    assert [row["number"] for row in out] == [149, 150]  # свежие обновления первыми
    assert set(out[0]) == {"repo", "number", "title", "state", "updated", "labels"}
    assert out[0]["labels"] == ["needs-human:triage", "phase:classified"]
    assert out[0]["updated"] == datetime(2026, 1, 3).isoformat()


# --- Правка 2: отказы GitHub оборачиваются контекстом, причина не теряется -


class RaisingIssue:
    def create_comment(self, body):
        raise RuntimeError("сеть легла")

    def add_to_labels(self, label):
        raise RuntimeError("сеть легла")

    def remove_from_labels(self, label):
        raise RuntimeError("сеть легла")

    @property
    def labels(self):
        raise RuntimeError("сеть легла")


class RaisingRepo:
    def get_issue(self, number):
        return RaisingIssue()

    def get_issues(self, **kw):
        raise RuntimeError("сеть легла")


class RaisingGithubClient:
    def get_repo(self, name):
        return RaisingRepo()


class PartlyRaisingGithubClient:
    """get_repo(name) отдаёт нормальный подставной репозиторий для всех
    имён, кроме перечисленных в `fail_repos` — для них `get_issues()`
    бросает исключение. Нужен, чтобы отличить отказ ОДНОГО репозитория от
    отказа обхода целиком."""

    def __init__(self, repos: dict, fail_repos=None):
        self._repos = repos
        self._fail_repos = fail_repos or set()

    def get_repo(self, name):
        if name in self._fail_repos:
            return RaisingRepo()
        return self._repos[name]


def test_github_labels_failure_wrapped_with_context():
    gh = GitHub("token", client=RaisingGithubClient())
    with pytest.raises(RuntimeError, match=r"o/r#149") as excinfo:
        gh.labels("o/r", 149)
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_github_comment_failure_wrapped_with_context():
    gh = GitHub("token", client=RaisingGithubClient())
    with pytest.raises(RuntimeError, match=r"o/r#149") as excinfo:
        gh.comment("o/r", 149, "текст")
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_github_add_label_failure_wrapped_with_context():
    gh = GitHub("token", client=RaisingGithubClient())
    with pytest.raises(RuntimeError, match="research-me") as excinfo:
        gh.add_label("o/r", 149, "research-me")
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_github_remove_label_failure_wrapped_with_context():
    gh = GitHub("token", client=RaisingGithubClient())
    with pytest.raises(RuntimeError, match="research-me") as excinfo:
        gh.remove_label("o/r", 149, "research-me")
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_github_parked_repo_failure_is_recorded_not_raised_and_does_not_block_rest():
    """Находка повторного ревью (тонкость 1, симметрично `pump_github`):
    раньше отказ `get_issues()` для одного репозитория поднимался наверх и
    обрывал внешний `for repo in repos` целиком — репозитории после
    сломанного не обрабатывались вовсе. Это буквальное повторение беды
    обслуживаемого контура (171 падение подряд из-за одного недоступного
    репозитория). Теперь отказ репозитория ловится поштучно и записывается
    в `problems` с номером задачи 0 (репозиторий недоступен целиком —
    номера конкретной задачи нет), а обход продолжается."""
    healthy = FakeRepo([
        FakeIssue(151, "Здоровая", ["needs-human:triage", "phase:classified"]),
    ])
    client = PartlyRaisingGithubClient({"o/healthy": healthy}, fail_repos={"o/broken"})
    gh = GitHub("token", client=client)
    problems: list[tuple[int, str]] = []

    result = gh.parked(["o/broken", "o/healthy"], problems)

    assert result == [Parked(repo="o/healthy", issue=151, title="Здоровая", phase="classified")]
    assert len(problems) == 1
    number, reason = problems[0]
    assert number == 0, "у отказа репозитория нет номера конкретной задачи — используется 0"
    assert "o/broken" in reason and "сеть легла" in reason


def test_github_recent_issues_failure_wrapped_with_context():
    gh = GitHub("token", client=RaisingGithubClient())
    with pytest.raises(RuntimeError, match="o/r") as excinfo:
        gh.recent_issues("o/r")
    assert "сеть легла" in str(excinfo.value.__cause__)


# --- Итоговый обзор, правка №5: объект репозитория кэшируется -------------


def test_repo_object_is_fetched_once_and_reused_across_calls(fake_repo):
    """Без кэша каждый вызов `_issue` (через `labels`/`add_label`/
    `remove_label`/`comment`) заново звал `get_repo()` — лишний HTTP-запрос
    на каждое обращение. Уборка одна дёргала его дважды на каждый открытый
    опрос (`get_repo` + `get_issue`); при непрерывном обходе это грозило
    упереться в лимит GitHub 5000/час."""
    client = FakeGithubClient({"o/r": fake_repo})
    gh = GitHub("token", client=client)

    gh.labels("o/r", 149)
    gh.labels("o/r", 150)
    gh.labels("o/r", 151)

    assert client.requested_repos == ["o/r"], "get_repo обязан звонить один раз на репозиторий"


# --- client= позволяет подставлять подделку без сети -----------------------


def test_github_uses_injected_client_without_touching_pygithub():
    fake = FakeGithubClient({})
    gh = GitHub("unused-token", client=fake)
    assert gh._gh is fake
