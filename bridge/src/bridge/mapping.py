import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bridge.render import Choice

SCHEMA = """
CREATE TABLE IF NOT EXISTS thread (
  repo TEXT NOT NULL, issue INTEGER NOT NULL, status_id TEXT NOT NULL,
  PRIMARY KEY (repo, issue));
CREATE TABLE IF NOT EXISTS poll (
  poll_id TEXT PRIMARY KEY, status_id TEXT NOT NULL,
  repo TEXT NOT NULL, issue INTEGER NOT NULL,
  choices TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
  cleaned INTEGER NOT NULL DEFAULT 0,
  phase TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS timing (
  poll_id TEXT PRIMARY KEY, posted_at REAL NOT NULL, decided_at REAL);
"""


@dataclass(frozen=True)
class PollLink:
    poll_id: str
    status_id: str
    repo: str
    issue: int
    choices: list[Choice]
    phase: str

    def label_for(self, title: str) -> str | None:
        """Голос приходит заголовком варианта — метку контура ищем по нему."""
        for choice in self.choices:
            if choice.title == title:
                return choice.label
        return None


class Mapping:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """`CREATE TABLE IF NOT EXISTS` создаёт новые столбцы только для новой
        базы — уже существующие базы (созданные до соответствующей правки)
        получают их здесь, а не молча теряют учёт.

        `phase` (итоговый обзор, правка №2/№3): без фазы, под которую опрос
        публиковался, `pump_votes` не может сверить, что задача не уехала в
        другую фазу за время, пока опрос висел, а `mark_cleaned` не может
        снять ключ развилки этой фазы после уборки. У баз, заведённых до этой
        правки, фаза неизвестна — пустая строка `''` не совпадёт ни с одной
        настоящей фазой, поэтому такие старые опросы просто не пройдут сверку
        фазы (голос будет отклонён как «не по адресу»), что безопаснее, чем
        угадывать."""
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(poll)")}
        if "cleaned" not in cols:
            self._db.execute(
                "ALTER TABLE poll ADD COLUMN cleaned INTEGER NOT NULL DEFAULT 0"
            )
        if "phase" not in cols:
            self._db.execute(
                "ALTER TABLE poll ADD COLUMN phase TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        """Закрыть соединение с базой. Без этого под `pytest -W error`
        всплывает ResourceWarning: unclosed database."""
        self._db.close()

    def remember_thread(self, repo: str, issue: int, status_id: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO thread(repo, issue, status_id) VALUES (?,?,?)",
            (repo, issue, status_id),
        )
        self._db.commit()

    def thread_for(self, repo: str, issue: int) -> str | None:
        row = self._db.execute(
            "SELECT status_id FROM thread WHERE repo=? AND issue=?", (repo, issue)
        ).fetchone()
        return row[0] if row else None

    def remember_poll(
        self, poll_id: str, status_id: str, repo: str, issue: int,
        choices: list[Choice], *, phase: str,
    ) -> None:
        """`phase` — фаза задачи, под которую опубликован опрос.
        Keyword-only и без значения по умолчанию нарочно: забыть её —
        значит лишить `pump_votes` возможности сверить, что задача не уехала
        в другую фазу, пока опрос висел (итоговый обзор, правка №2)."""
        payload = json.dumps(
            [{"title": c.title, "label": c.label} for c in choices],
            ensure_ascii=False,
        )
        self._db.execute(
            "INSERT INTO poll(poll_id,status_id,repo,issue,choices,closed,phase)"
            " VALUES (?,?,?,?,?,0,?)"
            " ON CONFLICT(poll_id) DO UPDATE SET"
            " status_id=excluded.status_id, repo=excluded.repo,"
            " issue=excluded.issue, choices=excluded.choices,"
            " phase=excluded.phase",
            (poll_id, status_id, repo, issue, payload, phase),
        )
        self._db.commit()

    def open_polls(self) -> list[PollLink]:
        rows = self._db.execute(
            "SELECT poll_id,status_id,repo,issue,choices,phase FROM poll WHERE closed=0"
        ).fetchall()
        return [
            PollLink(
                poll_id=r[0], status_id=r[1], repo=r[2], issue=int(r[3]),
                choices=[Choice(**c) for c in json.loads(r[4])], phase=r[5],
            )
            for r in rows
        ]

    def close_poll(self, poll_id: str) -> None:
        self._db.execute("UPDATE poll SET closed=1 WHERE poll_id=?", (poll_id,))
        self._db.commit()

    def decided_uncleaned(self) -> list[tuple[str, int]]:
        """(repo, issue) с закрытым опросом, который уборка ещё не
        проверяла. Без фильтра по `cleaned` цель уборки растёт без границ:
        мост навсегда дёргал бы GitHub по каждой когда-либо решённой
        задаче, даже если метка снята год назад."""
        rows = self._db.execute(
            "SELECT DISTINCT repo, issue FROM poll WHERE closed=1 AND cleaned=0"
        ).fetchall()
        return [(row[0], int(row[1])) for row in rows]

    def mark_cleaned(self, repo: str, issue: int) -> None:
        """Задача убрана — уборка по ней отработала (метка снята или снимать
        было нечего, см. `pump_cleanup`: убранной задача считается только
        тогда, когда она действительно перестала ждать человека). Больше в
        decided_uncleaned() не попадёт.

        Итоговый обзор, правка №3: цикл «развилка → голос → уборка» этим не
        заканчивается — ключ `decision:{repo}:{issue}:{phase}` в `seen`
        обязан быть снят вместе с уборкой, иначе он вечен, и если задача
        когда-нибудь вернётся в ту же фазу (например по «не дубликат» или
        после эскалации), `pump_decisions` эту развилку молча пропустит —
        решит, что она «уже была». Фазу берём у последнего решённого и ещё
        не убранного опроса этой задачи (самого свежего по `decided_at`):
        это и есть тот цикл, который уборка сейчас закрывает."""
        row = self._db.execute(
            "SELECT poll.phase FROM poll LEFT JOIN timing"
            " ON timing.poll_id = poll.poll_id"
            " WHERE poll.closed=1 AND poll.cleaned=0"
            " AND poll.repo=? AND poll.issue=?"
            " ORDER BY timing.decided_at DESC LIMIT 1",
            (repo, issue),
        ).fetchone()
        self._db.execute(
            "UPDATE poll SET cleaned=1 WHERE closed=1 AND repo=? AND issue=?",
            (repo, issue),
        )
        if row is not None and row[0]:
            self._db.execute(
                "DELETE FROM seen WHERE key=?",
                (f"decision:{repo}:{issue}:{row[0]}",),
            )
        self._db.commit()

    def seen(self, key: str) -> bool:
        return (
            self._db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone()
            is not None
        )

    def mark_seen(self, key: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO seen(key) VALUES (?)", (key,))
        self._db.commit()

    def forget_seen(self, key: str) -> None:
        """Снять отметку «сделано». Нужно pump_decisions: отметка там
        ставится ДО публикации, и если публикация отказала, её надо снять —
        иначе развилка, которую физически не задали, никогда не повторится."""
        self._db.execute("DELETE FROM seen WHERE key=?", (key,))
        self._db.commit()

    def mark_posted(self, poll_id: str, when: float) -> None:
        """Момент публикации развилки — первый обход фиксирует его, повторные
        обходы того же цикла перекачки (задачи 6-7) не должны ни переносить
        posted_at на последний обход, ни стирать уже проставленный
        decided_at обратно в NULL."""
        self._db.execute(
            "INSERT INTO timing(poll_id, posted_at, decided_at) VALUES (?,?,NULL)"
            " ON CONFLICT(poll_id) DO NOTHING",
            (poll_id, when),
        )
        self._db.commit()

    def mark_decided(self, poll_id: str, when: float) -> None:
        self._db.execute(
            "UPDATE timing SET decided_at=? WHERE poll_id=?", (when, poll_id)
        )
        self._db.commit()

    def timings(self) -> list[tuple[float, float]]:
        return [
            (float(r[0]), float(r[1]))
            for r in self._db.execute(
                "SELECT posted_at, decided_at FROM timing WHERE decided_at IS NOT NULL"
            ).fetchall()
        ]

    def pending_count(self) -> int:
        """Число развилок, которые ОПУБЛИКОВАНЫ, но пока не решены
        (`decided_at` пуст) — на МОМЕНТ ВЫЗОВА, не за всё время.

        Было названо `undecided_count`, докстринг обещал «увела в эскалацию
        по таймауту» — итоговый обзор указал, что это неправда: считаются
        ВСЕ развилки без `decided_at`, включая опубликованную секунду назад
        и ещё честно дожидающуюся голоса. Отличить «висит сейчас» от
        «умерла по таймауту» по одним `posted_at`/`decided_at` нельзя — для
        этого нужно было бы отдельно спрашивать состояние задачи в GitHub, а
        это дороже, чем переименовать функцию и перестать врать в тексте.
        Выбран дешёвый путь: имя и докстринг честно говорят, что считается
        число ещё не решённых прямо сейчас, а не число умерших по таймауту.

        Без этого числа отчёт о простое (metrics.report) видел бы только
        timings() — то есть только решённые развилки — и печатал бы
        красивую медиану, ни словом не упоминая, что часть развилок пока
        (или навсегда) осталась без ответа."""
        row = self._db.execute(
            "SELECT COUNT(*) FROM timing WHERE decided_at IS NULL"
        ).fetchone()
        return int(row[0])
