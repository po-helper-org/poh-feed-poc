import pytest

from bridge.config import AGENTS, Settings


def _set_tokens(monkeypatch):
    for agent in AGENTS:
        monkeypatch.setenv(f"FEED_TOKEN_{agent.upper()}", "tok")


def test_missing_github_token_fails_loudly(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("BRIDGE_REPOS", "o/r")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        Settings.load()


# --- Итоговый обзор, правка №7: BRIDGE_REPOS обязателен, дефолта нет -------


def test_missing_bridge_repos_fails_loudly_and_has_no_default(monkeypatch):
    """Раньше дефолт указывал на настоящий рабочий репозиторий владельца —
    мост, запущенный без настройки, начинал бы писать метки и комментарии в
    него. Теперь переменная обязательна, симметрично GITHUB_TOKEN."""
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("BRIDGE_REPOS", raising=False)
    with pytest.raises(RuntimeError, match="BRIDGE_REPOS"):
        Settings.load()


def test_blank_bridge_repos_is_treated_as_missing(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "   ")
    with pytest.raises(RuntimeError, match="BRIDGE_REPOS"):
        Settings.load()


def test_bridge_repos_parses_comma_separated_list(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "o/a, o/b ,o/c")
    assert Settings.load().repos == ("o/a", "o/b", "o/c")


# --- Итоговый обзор, правка №5: пауза цикла настраиваема -------------------


def test_cycle_seconds_defaults_to_thirty(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "o/r")
    monkeypatch.delenv("BRIDGE_CYCLE_SECONDS", raising=False)
    assert Settings.load().cycle_seconds == 30


def test_cycle_seconds_reads_env_override(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "o/r")
    monkeypatch.setenv("BRIDGE_CYCLE_SECONDS", "45")
    assert Settings.load().cycle_seconds == 45


def test_cycle_seconds_rejects_non_positive(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "o/r")
    monkeypatch.setenv("BRIDGE_CYCLE_SECONDS", "0")
    with pytest.raises(RuntimeError, match="BRIDGE_CYCLE_SECONDS"):
        Settings.load()


def test_cycle_seconds_rejects_garbage(monkeypatch):
    _set_tokens(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("BRIDGE_REPOS", "o/r")
    monkeypatch.setenv("BRIDGE_CYCLE_SECONDS", "скоро")
    with pytest.raises(RuntimeError, match="BRIDGE_CYCLE_SECONDS"):
        Settings.load()
