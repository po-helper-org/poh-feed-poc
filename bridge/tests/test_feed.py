from pathlib import Path
import pytest
from bridge.feed import Feed
from bridge.render import PollPost


class FakeClient:
    """Подделка Mastodon.py. Записывает вызовы, чтобы проверить не только
    результат, но и ФАКТ передачи нужных аргументов."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.next_id = 100

    def status_post(self, status, **kw):
        self.calls.append(("status_post", status, kw))
        self.next_id += 1
        out = {"id": str(self.next_id)}
        if kw.get("poll"):
            out["poll"] = {"id": f"P{self.next_id}"}
        return out

    def make_poll(self, options, expires_in):
        self.calls.append(("make_poll", options, expires_in))
        return {"options": options, "expires_in": expires_in}

    def media_post(self, path, description=None):
        self.calls.append(("media_post", path, description))
        self.next_id += 1
        return {"id": f"M{self.next_id}"}

    def poll(self, poll_id):
        self.calls.append(("poll", poll_id))
        return {
            "id": poll_id,
            "options": [
                {"title": "Сессия", "votes_count": 1},
                {"title": "БД", "votes_count": 0},
            ],
        }


@pytest.fixture
def feed():
    clients = {"issue_agent": FakeClient(), "howtodemo": FakeClient()}
    return Feed(clients=clients), clients


def test_post_is_unlisted_not_private(feed):
    """`private` виден только подписчикам, а подписка агента на агента уходит в
    ожидание одобрения — по такому посту нельзя даже проголосовать (404)."""
    f, clients = feed
    sid = f.post("issue_agent", "привет")
    assert sid == "101"
    name, status, kw = clients["issue_agent"].calls[0]
    assert status == "привет"
    assert kw["visibility"] == "unlisted"


def test_post_passes_spoiler_and_markdown(feed):
    f, clients = feed
    f.post("issue_agent", "## тело", spoiler="БФТ · 10 слов")
    _, _, kw = clients["issue_agent"].calls[0]
    assert kw["spoiler_text"] == "БФТ · 10 слов"
    assert kw["content_type"] == "text/markdown"


def test_poll_returns_both_ids(feed):
    f, clients = feed
    p = PollPost(text="вопрос", options=["Сессия", "БД"], expires_in=600)
    status_id, poll_id = f.post_poll("issue_agent", p)
    assert status_id == "101" and poll_id == "P101"
    assert ("make_poll", ["Сессия", "БД"], 600) in clients["issue_agent"].calls


def test_poll_without_deadline_gets_a_long_one(feed):
    """Срок ожидания держит контур. Опрос не должен закрыться сам и сделать
    решение недоступным."""
    f, clients = feed
    f.post_poll("issue_agent", PollPost(text="в", options=["да", "нет"]))
    name, options, expires_in = clients["issue_agent"].calls[0]
    assert name == "make_poll" and expires_in >= 7 * 24 * 3600


def test_media_refuses_to_be_mixed_with_poll(feed, tmp_path: Path):
    f, _ = feed
    shot = tmp_path / "a.png"
    shot.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="вложение"):
        f.post_media("howtodemo", "отчёт", [shot], poll=PollPost("q", ["a"], 60))


def test_votes_are_returned_by_option_title(feed):
    f, _ = feed
    assert f.votes("P101") == {"Сессия": 1, "БД": 0}


def test_unknown_agent_fails_loudly(feed):
    f, _ = feed
    with pytest.raises(KeyError, match="delivery"):
        f.post("delivery", "текст")
