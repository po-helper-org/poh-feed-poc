import sys
import time

from bridge.config import Settings
from bridge.feed import Feed
from bridge.harness import GitHub
from bridge.mapping import Mapping
from bridge.pump import pump_cleanup, pump_decisions, pump_votes


def main() -> int:
    settings = Settings.load()
    feed = Feed.from_settings(settings)
    mapping = Mapping(settings.db_path)
    github = GitHub(settings.github_token)

    once = "--once" in sys.argv
    while True:
        problems: list[tuple[int, str]] = []
        try:
            made = pump_decisions(
                feed, mapping, lambda: github.parked(settings.repos, problems)
            )
            sent = pump_votes(feed, mapping, github)
            touched = [
                (link.repo, link.issue) for link in mapping.open_polls()
            ] + [(repo, issue) for repo, issue in _decided(mapping)]
            cleaned = pump_cleanup(github, lambda: touched)
            if made or sent or cleaned:
                print(f"развилок: {made}, решений: {sent}, снято меток: {cleaned}")
            # Задачи, которые разобрать не удалось, обязаны быть названы:
            # молча пропущенная развилка — это работа, стоящая без причины.
            for number, reason in problems:
                print(f"задача #{number} пропущена: {reason}", file=sys.stderr)
        except Exception as error:  # цикл не должен умирать от одного отказа
            print(f"обход не удался: {error}", file=sys.stderr)
        if once:
            return 0
        time.sleep(5)


def _decided(mapping: Mapping) -> list[tuple[str, int]]:
    """Задачи, по которым решение уже отправлено: у них могла залипнуть метка."""
    rows = mapping._db.execute(
        "SELECT DISTINCT repo, issue FROM poll WHERE closed=1"
    ).fetchall()
    return [(row[0], int(row[1])) for row in rows]
