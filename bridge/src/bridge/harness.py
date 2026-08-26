from dataclasses import dataclass

PHASE_PREFIX = "phase:"
NEEDS_HUMAN = "needs-human:triage"

# Пять меток решения человека — полный набор, который понимает контур.
DECISION_LABELS = frozenset(
    {"research-me", "bug-me", "build-me", "not-duplicate", "confirm-duplicate"}
)


@dataclass(frozen=True)
class Parked:
    repo: str
    issue: int
    title: str
    phase: str


def phase_of(labels: list[str]) -> str | None:
    for label in labels:
        if label.startswith(PHASE_PREFIX):
            return label[len(PHASE_PREFIX):]
    return None


def is_parked(labels: list[str]) -> bool:
    return NEEDS_HUMAN in labels


def parked_from(issues: list[dict]) -> list[Parked]:
    """Задачи, ждущие человека. Без метки фазы задача пропускается: неизвестно,
    какие метки решения допустимы, а неподходящая игнорируется контуром молча."""
    out = []
    for issue in issues:
        labels = list(issue.get("labels", []))
        if not is_parked(labels):
            continue
        phase = phase_of(labels)
        if phase is None:
            continue
        out.append(Parked(
            repo=issue["repo"], issue=int(issue["number"]),
            title=issue["title"], phase=phase,
        ))
    return out


def stale_decision_labels(labels: list[str]) -> list[str]:
    """Контур метку решения никогда не снимает сам. Пока она висит, повторная
    постановка той же метки не породит события, и следующая развилка той же
    фазы не сработает. Снимаем, как только ожидание закончилось."""
    if is_parked(labels):
        return []
    return [label for label in labels if label in DECISION_LABELS]


class GitHub:
    """Тонкая обёртка. Вся логика — в чистых функциях выше."""

    def __init__(self, token: str):
        from github import Github

        self._gh = Github(token)

    def _issue(self, repo: str, number: int):
        return self._gh.get_repo(repo).get_issue(number)

    def labels(self, repo: str, issue: int) -> list[str]:
        return [label.name for label in self._issue(repo, issue).labels]

    def parked(self, repos) -> list[Parked]:
        rows = []
        for repo in repos:
            for issue in self._gh.get_repo(repo).get_issues(
                state="open", labels=[NEEDS_HUMAN]
            ):
                rows.append({
                    "repo": repo, "number": issue.number, "title": issue.title,
                    "labels": [label.name for label in issue.labels],
                })
        return parked_from(rows)

    def recent_issues(self, repo: str, limit: int = 20) -> list[dict]:
        out = []
        for issue in self._gh.get_repo(repo).get_issues(state="all", sort="updated")[:limit]:
            out.append({
                "repo": repo, "number": issue.number, "title": issue.title,
                "state": issue.state, "updated": issue.updated_at.isoformat(),
                "labels": [label.name for label in issue.labels],
            })
        return out

    def comment(self, repo: str, issue: int, body: str) -> None:
        self._issue(repo, issue).create_comment(body)

    def add_label(self, repo: str, issue: int, label: str) -> None:
        self._issue(repo, issue).add_to_labels(label)

    def remove_label(self, repo: str, issue: int, label: str) -> None:
        self._issue(repo, issue).remove_from_labels(label)
