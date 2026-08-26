from pathlib import Path
from bridge.render import PollPost


class Feed:
    """Обёртка над Mastodon.py. Один клиент на агента — каждый постит от себя."""

    def __init__(self, clients: dict[str, object]):
        self._clients = clients

    @staticmethod
    def from_settings(settings) -> "Feed":
        from mastodon import Mastodon

        clients = {
            agent: Mastodon(access_token=token, api_base_url=settings.feed_url)
            for agent, token in settings.tokens.items()
        }
        return Feed(clients)

    def _client(self, agent: str):
        if agent not in self._clients:
            raise KeyError(f"нет клиента для агента {agent!r}")
        return self._clients[agent]

    def post(
        self, agent: str, text: str, *, spoiler: str | None = None,
        in_reply_to: str | None = None,
    ) -> str:
        kw = {"visibility": "unlisted", "content_type": "text/markdown"}
        if spoiler:
            kw["spoiler_text"] = spoiler
        if in_reply_to:
            kw["in_reply_to_id"] = in_reply_to
        return str(self._client(agent).status_post(text, **kw)["id"])

    def post_poll(
        self, agent: str, poll: PollPost, *, in_reply_to: str | None = None
    ) -> tuple[str, str]:
        client = self._client(agent)
        # У развилки срока нет: его держит контур, а не лента. Ставим предельно
        # долгий, чтобы опрос не закрылся сам и не сделал решение недоступным.
        made = client.make_poll(
            poll.options, expires_in=poll.expires_in or 7 * 24 * 3600
        )
        kw = {"visibility": "unlisted", "poll": made}
        if in_reply_to:
            kw["in_reply_to_id"] = in_reply_to
        result = client.status_post(poll.text, **kw)
        return str(result["id"]), str(result["poll"]["id"])

    def post_media(
        self, agent: str, text: str, files: list[Path], *,
        in_reply_to: str | None = None, poll: PollPost | None = None,
    ) -> str:
        if poll is not None:
            raise ValueError(
                "вложение и опрос несовместимы в одном посте: "
                "отчёт и вопрос по нему публикуются двумя постами"
            )
        client = self._client(agent)
        ids = [client.media_post(str(p), description=p.stem)["id"] for p in files]
        kw = {"visibility": "unlisted", "media_ids": ids}
        if in_reply_to:
            kw["in_reply_to_id"] = in_reply_to
        return str(client.status_post(text, **kw)["id"])

    def votes(self, poll_id: str) -> dict[str, int]:
        any_client = next(iter(self._clients.values()))
        data = any_client.poll(poll_id)
        return {o["title"]: int(o["votes_count"]) for o in data["options"]}
