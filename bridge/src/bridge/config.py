import os
from dataclasses import dataclass
from pathlib import Path

AGENTS = ("issue_agent", "openhands", "pr_agent", "howtodemo", "delivery", "harness")


@dataclass(frozen=True)
class Settings:
    feed_url: str
    tokens: dict[str, str]
    db_path: Path
    github_token: str
    repos: tuple[str, ...]

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
        return Settings(
            feed_url=os.environ.get("FEED_URL", "http://127.0.0.1:8080"),
            tokens=tokens,
            db_path=Path(os.environ.get("BRIDGE_DB", "bridge.db")),
            github_token=github_token,
            repos=tuple(
                r.strip()
                for r in os.environ.get(
                    "BRIDGE_REPOS", "po-helper-org/poh-demo-checkout"
                ).split(",")
                if r.strip()
            ),
        )
