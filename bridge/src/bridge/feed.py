from pathlib import Path
from bridge.labels import Label, load_labels, with_labels
from bridge.render import POLL_LIFETIME_SECONDS, PollPost


# Видимость постов ленты. Путь сюда занял три шага, и каждый — живая проверка:
#   private  — виден только подписчикам, подписка агента на агента уходит в
#              ожидание одобрения, и по такому посту нельзя ни прочитать
#              статус, ни проголосовать: сервер отвечает 404 на оба;
#   unlisted — голосуется, но НЕ попадает ни в публичную ленту инстанса, ни в
#              ленты по хэштегу. Панель шорткатов, собранная на тегах, при
#              такой видимости пуста;
#   public   — работает всё. Наружу при этом ничего не уходит: инстанс слушает
#              петлю, федерация закрыта, публичная лента анонимам не отдаётся.
VISIBILITY = "public"


class Feed:
    """Обёртка над Mastodon.py. Один клиент на агента — каждый постит от себя."""

    def __init__(self, clients: dict[str, object], labels: list[Label] | None = None):
        self._clients = clients
        # Метки ставятся ЗДЕСЬ, в единственной точке, через которую текст
        # уходит в ленту, — а не в каждом вызывающем. Пост с опросом или
        # вложением потом безопасно не перемаркировать (правка без повтора
        # опроса убивает опрос — проверено), поэтому при публикации метки
        # обязаны стоять сразу.
        self._labels = labels or []

    def _labeled(self, agent: str, text: str) -> str:
        return with_labels(self._labels, agent, text)

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
        kw = {"visibility": VISIBILITY, "content_type": "text/markdown"}
        if spoiler:
            kw["spoiler_text"] = spoiler
        if in_reply_to:
            kw["in_reply_to_id"] = in_reply_to
        client = self._client(agent)
        try:
            return str(client.status_post(self._labeled(agent, text), **kw)["id"])
        except Exception as error:
            raise RuntimeError(
                f"лента: пост от агента {agent!r} не удался: {error}"
            ) from error

    def post_poll(
        self, agent: str, poll: PollPost, *, in_reply_to: str | None = None
    ) -> tuple[str, str]:
        client = self._client(agent)
        try:
            # API ленты требует `expires_in` обязательным полем — опрос не
            # может быть технически бессрочным (см. `POLL_LIFETIME_SECONDS`
            # в `render.py`). `render_decision` уже подставляет это значение
            # сам; запасной вариант здесь — страховка для любого другого
            # вызывающего, который его не выставил, чтобы опрос не закрылся
            # быстро и не сделал решение недоступным раньше срока контура.
            made = client.make_poll(
                poll.options, expires_in=poll.expires_in or POLL_LIFETIME_SECONDS
            )
            kw = {"visibility": VISIBILITY, "poll": made}
            if in_reply_to:
                kw["in_reply_to_id"] = in_reply_to
            result = client.status_post(self._labeled(agent, poll.text), **kw)
            return str(result["id"]), str(result["poll"]["id"])
        except Exception as error:
            raise RuntimeError(
                f"лента: опрос от агента {agent!r} не удался: {error}"
            ) from error

    def post_media(
        self, agent: str, text: str, files: list[Path], *,
        in_reply_to: str | None = None,
    ) -> str:
        # Итоговый обзор, правка №8: параметр `poll` существовал только
        # затем, чтобы бросить `ValueError` — ни один вызывающий никогда не
        # передавал его непустым (Mastodon API и так не принимает вложение и
        # опрос в одном посте — `publish_report` в `pump.py` публикует их
        # ДВУМЯ постами именно поэтому). Мёртвый параметр, живший только для
        # собственного теста, убран; ограничение платформы остаётся верным
        # само по себе — этот метод его просто не воспроизводит.
        client = self._client(agent)
        try:
            ids = [
                client.media_post(str(p), description=p.stem)["id"] for p in files
            ]
            kw = {"visibility": VISIBILITY, "media_ids": ids}
            if in_reply_to:
                kw["in_reply_to_id"] = in_reply_to
            return str(client.status_post(self._labeled(agent, text), **kw)["id"])
        except Exception as error:
            raise RuntimeError(
                f"лента: вложение от агента {agent!r} не удалось опубликовать: "
                f"{error}"
            ) from error

    def votes(self, poll_id: str) -> dict[str, int]:
        # Опрос виден и голосуется любым локальным клиентом, потому что
        # видимость поста — `unlisted` (см. post_poll): `private` был бы виден
        # только подписчикам, а подписка агента на агента уходит в ожидание
        # одобрения, так что клиент, никак не связанный с автором опроса,
        # читает счётчики так же исправно, как и клиент автора. Клиента для
        # чтения выбираем детерминированно — по отсортированному имени
        # агента, а не через `next(iter(...))`, который зависит от порядка
        # вставки в словарь (случайность реализации, а не контракт).
        reader_agent = min(self._clients)
        try:
            data = self._clients[reader_agent].poll(poll_id)
            return {o["title"]: int(o["votes_count"]) for o in data["options"]}
        except Exception as error:
            raise RuntimeError(
                f"лента: чтение голосов опроса {poll_id!r} клиентом "
                f"{reader_agent!r} не удалось: {error}"
            ) from error
