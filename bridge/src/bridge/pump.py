import time

from bridge.harness import stale_decision_labels
from bridge.mapping import Mapping
from bridge.render import question_for_phase, render_decision


def pump_decisions(feed, mapping: Mapping, read_parked) -> int:
    """Задачи, ждущие человека, превращает в посты с опросом. Одна задача —
    один пост: ключ идемпотентности не даёт повторному обходу плодить копии."""
    made = 0
    for parked in read_parked():
        key = f"decision:{parked.repo}:{parked.issue}:{parked.phase}"
        if mapping.seen(key):
            continue
        asked = question_for_phase(parked.phase)
        if asked is None:
            continue
        question, choices = asked
        post = render_decision(
            repo=parked.repo, issue=parked.issue, question=question, choices=choices
        )
        parent = mapping.thread_for(parked.repo, parked.issue)
        status_id, poll_id = feed.post_poll("issue_agent", post, in_reply_to=parent)
        if parent is None:
            mapping.remember_thread(parked.repo, parked.issue, status_id)
        mapping.remember_poll(poll_id, status_id, parked.repo, parked.issue, choices)
        mapping.mark_posted(poll_id, time.time())
        mapping.mark_seen(key)
        made += 1
    return made


def pump_votes(feed, mapping: Mapping, github) -> int:
    """Голос превращает в метку на Issue — той самой дверью, которой контур
    принимает решения человека. Метка ставится РОВНО один раз на опрос."""
    sent = 0
    for link in mapping.open_polls():
        counts = feed.votes(link.poll_id)
        chosen = [title for title, votes in counts.items() if votes > 0]
        if not chosen:
            continue
        title = chosen[0]
        label = link.label_for(title)
        key = f"vote:{link.poll_id}"
        if label is None or mapping.seen(key):
            mapping.close_poll(link.poll_id)
            continue
        github.add_label(link.repo, link.issue, label)
        github.comment(
            link.repo, link.issue,
            f"Решение принято через ленту контура: **{title}**.",
        )
        mapping.mark_seen(key)
        mapping.mark_decided(link.poll_id, time.time())
        mapping.close_poll(link.poll_id)
        sent += 1
    return sent


def pump_cleanup(github, read_touched) -> int:
    """Контур метку решения не снимает никогда. Пока она висит, повторная
    постановка той же метки не породит события — и следующая развилка той же
    фазы не сработает. Убираем, как только ожидание закончилось."""
    removed = 0
    for repo, issue in read_touched():
        for label in stale_decision_labels(github.labels(repo, issue)):
            github.remove_label(repo, issue, label)
            removed += 1
    return removed
