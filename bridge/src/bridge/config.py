import os
from dataclasses import dataclass
from pathlib import Path

AGENTS = ("issue_agent", "openhands", "pr_agent", "howtodemo", "delivery", "harness")


# Итоговый обзор, правка №5: сон между обходами непрерывного режима.
# Было 5 секунд наугад; с ростом числа обращений к GitHub (см. кэш
# репозитория в `harness.GitHub`) три открытых опроса на 5-секундном сне
# всё ещё упирались бы в лимит 5000 запросов/час на пределе. Для ленты,
# которую человек читает глазами, 30 секунд не удлиняют ощутимо ожидание,
# зато держат частоту обращений с большим запасом.
DEFAULT_CYCLE_SECONDS = 30


@dataclass(frozen=True)
class Settings:
    feed_url: str
    tokens: dict[str, str]
    db_path: Path
    github_token: str
    repos: tuple[str, ...]
    cycle_seconds: int
    labels_file: Path | None

    @staticmethod
    def load() -> "Settings":
        tokens = {}
        for agent in AGENTS:
            value = os.environ.get(f"FEED_TOKEN_{agent.upper()}")
            if not value:
                raise RuntimeError(
                    f"нет токена агента {agent}: переменная FEED_TOKEN_{agent.upper()} "
                    "пуста. Токены заводит scripts/create_agents.sh"
                )
            tokens[agent] = value
        github_token = os.environ.get("GITHUB_TOKEN", "")
        if not github_token:
            raise RuntimeError(
                "GITHUB_TOKEN пуст: мост доставляет решения человека постановкой "
                "метки на Issue, без доступа к GitHub он бесполезен"
            )
        # Итоговый обзор, правка №7: раньше был дефолт
        # `po-helper-org/poh-demo-checkout` — настоящий рабочий репозиторий
        # владельца. Мост, запущенный без настройки (забыли переменную,
        # опечатались в имени, скопировали чужой .env), НАЧИНАЛ БЫ ПИСАТЬ В
        # НЕГО МЕТКИ И КОММЕНТАРИИ. Дефолта больше нет — переменная
        # обязательна, симметрично `GITHUB_TOKEN` выше.
        repos_raw = os.environ.get("BRIDGE_REPOS", "")
        if not repos_raw.strip():
            raise RuntimeError(
                "BRIDGE_REPOS пуст: без него мост не знает, в какой "
                "репозиторий писать метки и комментарии. Указать явно, "
                "например po-helper-org/poh-demo-checkout — список через "
                "запятую, если репозиториев несколько"
            )
        repos = tuple(r.strip() for r in repos_raw.split(",") if r.strip())
        if not repos:
            raise RuntimeError(
                "BRIDGE_REPOS не пуст, но не содержит ни одного имени "
                "репозитория после разбора по запятой"
            )
        cycle_raw = os.environ.get("BRIDGE_CYCLE_SECONDS", "")
        try:
            cycle_seconds = int(cycle_raw) if cycle_raw.strip() else DEFAULT_CYCLE_SECONDS
        except ValueError as error:
            raise RuntimeError(
                f"BRIDGE_CYCLE_SECONDS={cycle_raw!r} не целое число секунд"
            ) from error
        if cycle_seconds <= 0:
            raise RuntimeError(
                f"BRIDGE_CYCLE_SECONDS={cycle_seconds} обязан быть положительным"
            )
        return Settings(
            feed_url=os.environ.get("FEED_URL", "http://127.0.0.1:8080"),
            tokens=tokens,
            db_path=Path(os.environ.get("BRIDGE_DB", "bridge.db")),
            github_token=github_token,
            repos=repos,
            cycle_seconds=cycle_seconds,
            # Метки не обязательны: без файла лента ведётся без них. Путь один
            # на всех писателей — тот же, что читает плагин dsh.
            labels_file=Path(os.environ["LABELS_FILE"]) if os.environ.get("LABELS_FILE") else None,
        )
