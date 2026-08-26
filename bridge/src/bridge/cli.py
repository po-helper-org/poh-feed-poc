import sys
import time

from bridge.config import Settings
from bridge.feed import Feed
from bridge.harness import GitHub
from bridge.mapping import Mapping
from bridge.pump import pump_cleanup, pump_decisions, pump_github, pump_votes


def main() -> int:
    settings = Settings.load()
    feed = Feed.from_settings(settings)
    mapping = Mapping(settings.db_path)
    github = GitHub(settings.github_token)

    once = "--once" in sys.argv
    while True:
        problems: list[tuple[int, str]] = []
        made = sent = cleaned = seen = 0

        # Каждый из четырёх циклов обёрнут отдельно: отказ одного не должен
        # прятать проблемы, накопленные другими, и не должен мешать им
        # отработать в этом же проходе.
        try:
            # У pump_decisions уже есть поштучный try/except внутри — сюда
            # попадает только непредвиденный отказ самого цикла (например,
            # github.parked() из-за недоступного репозитория).
            made = pump_decisions(
                feed, mapping, lambda: github.parked(settings.repos, problems), problems
            )
        except Exception as error:
            print(f"развилки: обход не удался: {error}", file=sys.stderr)

        try:
            sent = pump_votes(feed, mapping, github, problems)
        except Exception as error:  # у pump_votes уже есть поштучный
            # try/except внутри — сюда попадает только непредвиденный отказ
            # самого цикла (например, mapping.open_polls()).
            print(f"голоса: обход не удался: {error}", file=sys.stderr)

        try:
            # У pump_github уже есть поштучный try/except внутри — сюда
            # попадает только непредвиденный отказ самого цикла (например,
            # github.recent_issues() из-за недоступного репозитория, см.
            # docstring pump_github).
            seen = pump_github(feed, mapping, github, settings.repos, problems)
        except Exception as error:
            print(f"события GitHub: обход не удался: {error}", file=sys.stderr)

        try:
            touched = [
                (link.repo, link.issue) for link in mapping.open_polls()
            ] + mapping.decided_uncleaned()
            cleaned = pump_cleanup(
                github, lambda: touched, mapping.mark_cleaned, problems
            )
        except Exception as error:
            print(f"уборка: обход не удался: {error}", file=sys.stderr)

        if made or sent or seen or cleaned:
            print(
                f"развилок: {made}, решений: {sent}, "
                f"событий GitHub: {seen}, снято меток: {cleaned}"
            )
        # Задачи, которые разобрать не удалось, обязаны быть названы:
        # молча пропущенная развилка — это работа, стоящая без причины.
        # Печатаем после всех четырёх циклов и даже если один из них отказал —
        # иначе причины, накопленные уже отработавшими циклами, терялись бы
        # вместе с отказавшим.
        for number, reason in problems:
            print(f"задача #{number} пропущена: {reason}", file=sys.stderr)

        if once:
            return 0
        time.sleep(5)
