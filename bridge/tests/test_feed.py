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


def test_votes_are_returned_by_option_title(feed):
    f, _ = feed
    assert f.votes("P101") == {"Сессия": 1, "БД": 0}


def test_unknown_agent_fails_loudly(feed):
    f, _ = feed
    with pytest.raises(KeyError, match="delivery"):
        f.post("delivery", "текст")


def test_post_media_passes_ids_visibility_and_paths_as_strings(feed, tmp_path: Path):
    """Ветка отказа (вложение + опрос) была проверена и раньше, а основной
    путь — какие аргументы реально уходят в media_post и status_post — нет:
    подделка добрее реальности, пока это не закрыто фактом передачи."""
    f, clients = feed
    a = tmp_path / "график.png"
    a.write_bytes(b"\x89PNG")
    b = tmp_path / "лог.png"
    b.write_bytes(b"\x89PNG")

    sid = f.post_media("howtodemo", "отчёт", [a, b])

    calls = clients["howtodemo"].calls
    assert calls[0] == ("media_post", str(a), "график")
    assert calls[1] == ("media_post", str(b), "лог")
    name, status, kw = calls[2]
    assert name == "status_post" and status == "отчёт"
    assert kw["visibility"] == "unlisted"
    assert kw["media_ids"] == ["M101", "M102"]
    assert sid == "103"


def test_post_passes_in_reply_to(feed):
    f, clients = feed
    f.post("issue_agent", "ответ", in_reply_to="55")
    _, _, kw = clients["issue_agent"].calls[0]
    assert kw["in_reply_to_id"] == "55"


def test_post_poll_passes_in_reply_to(feed):
    f, clients = feed
    f.post_poll(
        "issue_agent", PollPost(text="в", options=["да", "нет"]), in_reply_to="55"
    )
    name, status, kw = clients["issue_agent"].calls[1]
    assert name == "status_post"
    assert kw["in_reply_to_id"] == "55"


def test_post_media_passes_in_reply_to(feed, tmp_path: Path):
    f, clients = feed
    shot = tmp_path / "a.png"
    shot.write_bytes(b"\x89PNG")
    f.post_media("howtodemo", "отчёт", [shot], in_reply_to="55")
    name, status, kw = clients["howtodemo"].calls[-1]
    assert name == "status_post"
    assert kw["in_reply_to_id"] == "55"


def test_votes_client_choice_does_not_depend_on_dict_insertion_order(feed):
    """`votes()` не должен зависеть от порядка обхода словаря клиентов — тот
    хранит порядок вставки, а не контракт. Один и тот же клиент выбирается
    для одного набора агентов независимо от того, в каком порядке их
    перечислили при создании Feed."""
    a, b = FakeClient(), FakeClient()
    forward = Feed(clients={"issue_agent": a, "howtodemo": b})
    backward = Feed(clients={"howtodemo": b, "issue_agent": a})

    forward.votes("P101")
    backward.votes("P101")

    assert b.calls == [("poll", "P101"), ("poll", "P101")]
    assert a.calls == []


class FailingClient(FakeClient):
    def status_post(self, status, **kw):
        raise RuntimeError("сеть легла")

    def make_poll(self, options, expires_in):
        raise RuntimeError("сеть легла")

    def media_post(self, path, description=None):
        raise RuntimeError("сеть легла")

    def poll(self, poll_id):
        raise RuntimeError("сеть легла")


class MalformedClient(FakeClient):
    """Отвечает без ожидаемого поля — источник голого KeyError('id')."""

    def status_post(self, status, **kw):
        self.calls.append(("status_post", status, kw))
        return {}


def test_post_failure_is_wrapped_with_agent_and_operation_context():
    f = Feed(clients={"issue_agent": FailingClient()})
    with pytest.raises(RuntimeError, match="issue_agent") as excinfo:
        f.post("issue_agent", "текст")
    assert excinfo.value.__cause__ is not None
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_post_malformed_response_wraps_bare_keyerror_with_context():
    f = Feed(clients={"issue_agent": MalformedClient()})
    with pytest.raises(RuntimeError, match="issue_agent") as excinfo:
        f.post("issue_agent", "текст")
    assert isinstance(excinfo.value.__cause__, KeyError)


def test_post_poll_failure_is_wrapped_with_agent_and_operation_context():
    f = Feed(clients={"issue_agent": FailingClient()})
    with pytest.raises(RuntimeError, match="issue_agent") as excinfo:
        f.post_poll("issue_agent", PollPost(text="в", options=["да", "нет"]))
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_post_media_failure_is_wrapped_with_agent_and_operation_context(
    tmp_path: Path,
):
    f = Feed(clients={"howtodemo": FailingClient()})
    shot = tmp_path / "a.png"
    shot.write_bytes(b"\x89PNG")
    with pytest.raises(RuntimeError, match="howtodemo") as excinfo:
        f.post_media("howtodemo", "отчёт", [shot])
    assert "сеть легла" in str(excinfo.value.__cause__)


def test_votes_failure_is_wrapped_with_poll_id_and_agent_context():
    f = Feed(clients={"issue_agent": FailingClient()})
    with pytest.raises(RuntimeError, match="P101") as excinfo:
        f.votes("P101")
    assert "сеть легла" in str(excinfo.value.__cause__)
