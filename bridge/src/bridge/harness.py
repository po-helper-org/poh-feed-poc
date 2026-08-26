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


def phase_of(labels: list[str], issue: int | None = None) -> str | None:
    """Метка фазы на задаче ровно одна — это инвариант контура. Если их
    оказалось несколько (инвариант нарушен), угадывать, какая верна, нельзя:
    неверная фаза даёт неверный набор вариантов решения, человек нажмёт
    кнопку, и контур эту метку молча проигнорирует — прогон останется стоять,
    а никто не узнает почему. Поэтому неоднозначность называется явно, вместе
    с номером задачи, если он известен вызывающему."""
    phases = [label[len(PHASE_PREFIX):] for label in labels if label.startswith(PHASE_PREFIX)]
    if len(phases) > 1:
        where = f"задача {issue}" if issue is not None else "задача"
        raise ValueError(f"{where}: несколько меток фазы: {phases}")
    return phases[0] if phases else None


def is_parked(labels: list[str]) -> bool:
    return NEEDS_HUMAN in labels


def parked_from(
    issues: list[dict], problems: list[tuple[int, str]] | None = None
) -> list[Parked]:
    """Задачи, ждущие человека. Без метки фазы или с несколькими метками фазы
    задача пропускается: неизвестно, какие метки решения допустимы, а
    неподходящая игнорируется контуром молча.

    Отказ по одной задаче не должен прерывать разбор остальных — контур,
    который мы обслуживаем, однажды уронил весь обход из-за одного плохого
    элемента (171 падение подряд). Если передан `problems`, в него
    дописывается пара (номер задачи, причина) для каждой пропущенной задачи;
    если не передан — они просто отбрасываются."""
    out = []
    for issue in issues:
        number = int(issue["number"])
        labels = list(issue.get("labels", []))
        if not is_parked(labels):
            continue
        try:
            phase = phase_of(labels, issue=number)
        except ValueError as error:
            if problems is not None:
                problems.append((number, str(error)))
            continue
        if phase is None:
            if problems is not None:
                problems.append((number, "нет метки фазы"))
            continue
        out.append(Parked(
            repo=issue["repo"], issue=number,
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

    def __init__(self, token: str, client=None):
        if client is not None:
            self._gh = client
        else:
            from github import Github

            self._gh = Github(token)
        # Итоговый обзор, правка №5: без кэша каждое обращение к задаче
        # (`_issue`, а через него `labels`/`comment`/`add_label`/
        # `remove_label`) заново звало `get_repo()` — лишний HTTP-запрос на
        # КАЖДЫЙ вызов, при том что объект репозитория не меняется между
        # вызовами в пределах жизни процесса моста. Уборка одна дёргала его
        # дважды на каждый открытый опрос (`get_repo` + `get_issue`); при
        # непрерывном обходе это грозило упереться в лимит GitHub 5000/час.
        # Кэш живёт всё время жизни `GitHub` (мост создаёт его один раз до
        # цикла в `cli.main`), поэтому `get_repo()` для конкретного
        # репозитория происходит не «раз за проход», а один раз вообще.
        self._repos: dict[str, object] = {}

    def _repo(self, repo: str):
        if repo not in self._repos:
            self._repos[repo] = self._gh.get_repo(repo)
        return self._repos[repo]

    def _issue(self, repo: str, number: int):
        return self._repo(repo).get_issue(number)

    def labels(self, repo: str, issue: int) -> list[str]:
        try:
            return [label.name for label in self._issue(repo, issue).labels]
        except Exception as error:
            raise RuntimeError(
                f"github: чтение меток задачи {repo}#{issue} не удалось: {error}"
            ) from error

    def parked(
        self, repos, problems: list[tuple[int, str]] | None = None
    ) -> list[Parked]:
        """`problems`, если передан, получает (номер задачи, причина) для
        каждой задачи, чью фазу не удалось разобрать — см. `parked_from`, — а
        также для каждого репозитория, поиск в котором отказал целиком.

        Правка повторного ревью: раньше отказ `get_issues()` для одного
        репозитория поднимался наверх и обрывал обход остальных
        репозиториев целиком — то же самое, из-за чего контур, который мы
        обслуживаем, однажды дал 171 падение подряд. Теперь отказ
        репозитория ловится поштучно, симметрично `pump_github`, и не
        мешает разбору остальных. У такого отказа нет номера конкретной
        задачи, поэтому используется сентинел 0 (подробное обоснование — в
        докстринге `pump_github`); имя репозитория остаётся в тексте
        причины."""
        rows = []
        for repo in repos:
            try:
                issues = list(
                    self._repo(repo).get_issues(
                        state="open", labels=[NEEDS_HUMAN]
                    )
                )
            except Exception as error:
                if problems is not None:
                    problems.append((
                        0,
                        f"github: поиск припаркованных задач в {repo!r} не удался: {error}",
                    ))
                continue
            for issue in issues:
                rows.append({
                    "repo": repo, "number": issue.number, "title": issue.title,
                    "labels": [label.name for label in issue.labels],
                })
        return parked_from(rows, problems)

    def recent_issues(self, repo: str, limit: int = 20) -> list[dict]:
        try:
            issues = self._repo(repo).get_issues(state="all", sort="updated")[:limit]
            out = []
            for issue in issues:
                out.append({
                    "repo": repo, "number": issue.number, "title": issue.title,
                    "state": issue.state, "updated": issue.updated_at.isoformat(),
                    "labels": [label.name for label in issue.labels],
                })
            return out
        except Exception as error:
            raise RuntimeError(
                f"github: чтение последних задач {repo!r} не удалось: {error}"
            ) from error

    def comment(self, repo: str, issue: int, body: str) -> None:
        try:
            self._issue(repo, issue).create_comment(body)
        except Exception as error:
            raise RuntimeError(
                f"github: комментарий к {repo}#{issue} не удался: {error}"
            ) from error

    def add_label(self, repo: str, issue: int, label: str) -> None:
        try:
            self._issue(repo, issue).add_to_labels(label)
        except Exception as error:
            raise RuntimeError(
                f"github: постановка метки {label!r} на {repo}#{issue} не "
                f"удалась: {error}"
            ) from error

    def remove_label(self, repo: str, issue: int, label: str) -> None:
        try:
            self._issue(repo, issue).remove_from_labels(label)
        except Exception as error:
            raise RuntimeError(
                f"github: снятие метки {label!r} с {repo}#{issue} не удалось: "
                f"{error}"
            ) from error
