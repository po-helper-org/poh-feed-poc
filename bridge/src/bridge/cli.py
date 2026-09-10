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
            # У pump_decisions уже есть поштучный try/except внутри, а у
            # github.parked() — свой, по репозиториям (см. docstring
            # GitHub.parked). Сюда попадает только непредвиденный отказ
            # самого цикла помимо этого.
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
            # У pump_github уже есть поштучный try/except внутри — как по
            # задачам, так и по репозиториям (github.recent_issues() из-за
            # недоступного репозитория теперь тоже ловится там, см.
            # docstring pump_github). Сюда попадает только непредвиденный
            # отказ самого цикла помимо этого.
            seen = pump_github(feed, mapping, github, settings.repos, problems)
        except Exception as error:
            print(f"события GitHub: обход не удался: {error}", file=sys.stderr)

        try:
            # Итоговый обзор, правка №8: цель уборки — только
            # `decided_uncleaned()`. Задача с ЕЩЁ ОТКРЫТЫМ опросом (первая
            # половина прежнего списка) заведомо ещё ждёт человека —
            # решение по ней не принято, `pump_votes` метку решения не
            # ставил, значит `stale_decision_labels` там по построению
            # ничего не найдёт. Дёргать GitHub ради проверки, которая не
            # может ничего снять, — лишний запрос без цели (см. правку №5:
            # именно эти лишние обращения упирались в лимит GitHub).
            cleaned = pump_cleanup(
                github, mapping.decided_uncleaned, mapping.mark_cleaned, problems
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
            # Номер 0 — признак «причина не про конкретную задачу»
            # (например, отказал целый репозиторий). Печатать такое как
            # «задача #0» значит сбивать с толку того, кто читает журнал.
            where = f"задача #{number}" if number else "обход"
            print(f"{where}: {reason}", file=sys.stderr)

        if once:
            return 0
        time.sleep(settings.cycle_seconds)


# Без этого блока `python -m bridge.cli` импортирует модуль, НИЧЕГО не
# выполняет и выходит с кодом 0 — молчаливый отказ ровно того рода, который
# мост и создан ловить: команда отработала, код успеха, результата нет.
# Точка входа `feed-bridge` из pyproject работала, а документированный запуск
# модулем — нет, и отличить их по выводу было невозможно.
if __name__ == "__main__":
    sys.exit(main())
