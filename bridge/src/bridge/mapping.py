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
  choices TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
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
        self._db.commit()

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
        choices: list[Choice],
    ) -> None:
        payload = json.dumps(
            [{"title": c.title, "label": c.label} for c in choices],
            ensure_ascii=False,
        )
        self._db.execute(
            "INSERT OR REPLACE INTO poll(poll_id,status_id,repo,issue,choices,closed)"
            " VALUES (?,?,?,?,?,0)",
            (poll_id, status_id, repo, issue, payload),
        )
        self._db.commit()

    def open_polls(self) -> list[PollLink]:
        rows = self._db.execute(
            "SELECT poll_id,status_id,repo,issue,choices FROM poll WHERE closed=0"
        ).fetchall()
        return [
            PollLink(
                poll_id=r[0], status_id=r[1], repo=r[2], issue=int(r[3]),
                choices=[Choice(**c) for c in json.loads(r[4])],
            )
            for r in rows
        ]

    def close_poll(self, poll_id: str) -> None:
        self._db.execute("UPDATE poll SET closed=1 WHERE poll_id=?", (poll_id,))
        self._db.commit()

    def seen(self, key: str) -> bool:
        return (
            self._db.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone()
            is not None
        )

    def mark_seen(self, key: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO seen(key) VALUES (?)", (key,))
        self._db.commit()

    def mark_posted(self, poll_id: str, when: float) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO timing(poll_id, posted_at, decided_at)"
            " VALUES (?,?,NULL)",
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
