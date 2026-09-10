import time
from pathlib import Path

from bridge.harness import is_parked, phase_of, stale_decision_labels
from bridge.mapping import Mapping
from bridge.render import (
    TAGS,
    question_for_phase,
    render_agent_reply,
    render_decision,
    render_report,
    unaccounted_sentences,
)


def pump_decisions(
    feed, mapping: Mapping, read_parked,
    problems: list[tuple[int, str]] | None = None,
) -> int:
    """Задачи, ждущие человека, превращает в посты с опросом. Одна задача —
    один пост: ключ идемпотентности не даёт повторному обходу плодить копии.

    Отметку «сделано» ставим ДО публикации, а не после. Риск здесь другой,
    чем у голоса в `pump_votes`: пост в ленте не идемпотентен (это не
    GitHub-метка — повтор создаёт вторую видимую запись), поэтому нельзя
    просто закрепить отметку сразу после вызова и забыть, как для метки.
    Вместо этого: если публикация ОТКАЗАЛА, отметку снимаем — следующий
    обход (через паузу цикла — `Settings.cycle_seconds`) попробует снова. Так потерянная развилка
    невозможна: либо она опубликована и учтена, либо отметка снята и
    попытка повторится. Небольшой дубль поста при редком отказе ровно в
    момент ответа сервера — куда меньшее зло, чем задача, навсегда забытая
    молча.

    Отказ по одной развилке не должен останавливать разбор остальных — так
    контур, который мы обслуживаем, однажды уронил весь обход из-за одного
    недоступного репозитория (171 падение подряд); переносить эту беду сюда
    нельзя. Причина отказа, если передан `problems`, уходит в него вместе с
    номером задачи — симметрично `pump_votes`/`pump_cleanup`.
    """
    made = 0
    # Фазы, для которых вопроса нет. Молча пропустить такую задачу нельзя:
    # человек её ждёт, метка `needs-human:triage` на ней висит, а в ленте она
    # не появится НИКОГДА — и узнать об этом будет неоткуда. Первый живой
    # прогон дал ровно это: 20 припаркованных задач, 1 развилка, 19 исчезли
    # без единого слова. Называем сводкой, а не строкой на задачу: строка на
    # задачу — это 19 одинаковых сообщений каждые 30 секунд, шум, который
    # перестают читать, и тогда молчание возвращается другим путём.
    unanswerable: dict[str, list[int]] = {}
    for parked in read_parked():
        key = f"decision:{parked.repo}:{parked.issue}:{parked.phase}"
        if mapping.seen(key):
            continue
        asked = question_for_phase(parked.phase)
        if asked is None:
            unanswerable.setdefault(parked.phase, []).append(parked.issue)
            continue
        question, choices = asked

        mapping.mark_seen(key)
        try:
            post = render_decision(
                repo=parked.repo, issue=parked.issue, question=question, choices=choices
            )
            parent = mapping.thread_for(parked.repo, parked.issue)
            status_id, poll_id = feed.post_poll("issue_agent", post, in_reply_to=parent)
            if parent is None:
                mapping.remember_thread(parked.repo, parked.issue, status_id)
            mapping.remember_poll(
                poll_id, status_id, parked.repo, parked.issue, choices,
                phase=parked.phase,
            )
            mapping.mark_posted(poll_id, time.time())
        except Exception as error:
            mapping.forget_seen(key)
            if problems is not None:
                problems.append((
                    parked.issue,
                    f"развилка {parked.repo}#{parked.issue}: публикация не удалась: {error}",
                ))
            continue
        made += 1
    if unanswerable and problems is not None:
        for phase, issues in sorted(unanswerable.items()):
            shown = ", ".join(f"#{i}" for i in issues[:5])
            more = f" и ещё {len(issues) - 5}" if len(issues) > 5 else ""
            problems.append((
                0,
                f"фаза {phase!r}: вопроса нет, задачи ждут человека вне ленты "
                f"({len(issues)}): {shown}{more}",
            ))
    return made


def pump_votes(feed, mapping: Mapping, github, problems: list[tuple[int, str]] | None = None) -> int:
    """Голос превращает в метку на Issue — той самой дверью, которой контур
    принимает решения человека. Выбираем вариант с НАИБОЛЬШИМ числом
    голосов, а не первый ненулевой по порядку опций — угадывать чужое
    большинство нельзя. При ничьей решение не наше: не действуем вовсе,
    причину называем в `problems`, опрос остаётся открытым — вдруг кто-то
    проголосует ещё и ничья разрешится сама.

    Если `link.label_for(title)` вернул `None` — голос отдан за заголовок,
    которого нет среди вариантов опроса, — метку не ставим и опрос не
    закрываем. Закрыть его в этой ветке значило бы молча потерять решение
    человека: он проголосовал, опрос исчез, а метка так и не появилась, и
    никто не узнал бы почему. Причина уходит в `problems` с номером задачи
    и полученным заголовком, опрос остаётся открытым для следующего обхода.

    Итоговый обзор, правка №2 (важно): перед постановкой метки читаем
    ТЕКУЩИЕ метки задачи и сверяем текущую фазу с той, под которую опрос
    публиковался (`link.phase`, см. `Mapping.remember_poll`). Пока опрос
    висел, обслуживаемая система могла увести задачу в другую фазу — тогда
    метка уходит не по адресу и контур игнорирует её МОЛЧА (см. докстринг
    `phase_of`/план), а мост при этом как ни в чём не бывало пишет
    «решений: 1», оставляет комментарий «Решение принято...» и зачитывает
    время в метрику простоя. Это отказ, выглядящий успехом сразу в трёх
    местах — ровно то, против чего построен весь инструмент. При
    расхождении фазы метку не ставим, комментарий не пишем, простой не
    засчитываем; причина уходит в `problems`, опрос закрывается — он уже не
    про текущее состояние задачи, оставлять его открытым нет смысла.

    Порядок действий обоснован разной ценой ошибки. Сверка фазы идёт ПЕРЕД
    `add_label`, а отметка «сделано» — сразу за ним, ДО комментария.
    Постановка уже стоящей метки в GitHub безвредна и не порождает нового
    события (контур сам никогда её не снимает, см. `stale_decision_labels`),
    поэтому повторный `add_label` ничего не ломает. Комментарий же видят
    люди — его дублирование недопустимо. Отметка, записанная сразу после
    метки, гарантирует, что повторный обход больше не увидит этот опрос
    вовсе (poll уже закрыт), даже если сам комментарий в этот раз отказал:
    отказ комментария — не повод откатывать уже принятое и записанное
    решение, поэтому он идёт последним действием «по возможности», своим
    отдельным try/except.

    Отказ по одному опросу не должен останавливать разбор остальных — так
    контур, который мы обслуживаем, однажды уронил весь обход из-за одного
    недоступного репозитория (171 падение подряд); переносить эту беду сюда
    нельзя.
    """
    sent = 0
    for link in mapping.open_polls():
        try:
            counts = feed.votes(link.poll_id)
            leading = [(title, v) for title, v in counts.items() if v > 0]
            if not leading:
                continue
            top = max(v for _, v in leading)
            winners = [title for title, v in leading if v == top]
            if len(winners) > 1:
                if problems is not None:
                    problems.append((
                        link.issue,
                        f"опрос {link.poll_id}: ничья голосов между "
                        f"{', '.join(winners)} — решение за человеком, опрос "
                        "остаётся открытым",
                    ))
                continue
            title = winners[0]
            label = link.label_for(title)
            if label is None:
                if problems is not None:
                    problems.append((
                        link.issue,
                        f"опрос {link.poll_id}: голос за «{title}» не соответствует ни "
                        "одному известному варианту — метка не поставлена, опрос "
                        "остаётся открытым",
                    ))
                continue

            key = f"vote:{link.poll_id}"
            if mapping.seen(key):
                mapping.close_poll(link.poll_id)
                continue

            current_phase = phase_of(github.labels(link.repo, link.issue), issue=link.issue)
            if current_phase != link.phase:
                if problems is not None:
                    problems.append((
                        link.issue,
                        f"опрос {link.poll_id}: задача уехала из фазы "
                        f"{link.phase!r} в {current_phase!r}, пока опрос "
                        "висел — голос не по адресу, метка не поставлена, "
                        "опрос закрыт как неактуальный",
                    ))
                mapping.close_poll(link.poll_id)
                continue

            github.add_label(link.repo, link.issue, label)
            mapping.mark_seen(key)
            mapping.close_poll(link.poll_id)
            mapping.mark_decided(link.poll_id, time.time())

            try:
                github.comment(
                    link.repo, link.issue,
                    f"Решение принято через ленту контура: **{title}**.",
                )
            except Exception as error:
                if problems is not None:
                    problems.append((
                        link.issue,
                        f"комментарий о решении не отправлен: {error}",
                    ))
            sent += 1
        except Exception as error:
            if problems is not None:
                problems.append((link.issue, str(error)))
    return sent


def pump_github(
    feed, mapping: Mapping, github, repos,
    problems: list[tuple[int, str]] | None = None,
) -> int:
    """Новая задача открывает ветку постом; изменение состояния отвечает в
    ту же ветку. Смысл — «одна задача = одна ветка», а не россыпь отдельных
    постов об одной и той же задаче.

    Ключ идемпотентности включает время обновления задачи
    (`issue['updated']`): без этого одно и то же состояние (например,
    «issue ещё открыт») публиковалось бы заново на каждом обходе, хотя
    ничего не изменилось.

    Отметку «сделано» ставим ДО публикации, а не после — симметрично
    `pump_decisions`. Пост в ленте не идемпотентен (это не GitHub-метка,
    повтор создаёт вторую видимую запись), поэтому при отказе публикации
    отметку снимаем: следующий обход (через паузу цикла — `Settings.cycle_seconds`) попробует снова.
    Так потерянное событие невозможно: либо оно опубликовано и учтено,
    либо отметка снята и попытка повторится.

    Отказ по одной задаче не должен останавливать разбор остальных — так
    контур, который мы обслуживаем, однажды уронил весь обход из-за одного
    недоступного репозитория (171 падение подряд); переносить эту беду сюда
    нельзя. Причина отказа, если передан `problems`, уходит в него вместе с
    номером задачи — симметрично `pump_decisions`/`pump_votes`/`pump_cleanup`.

    Правка повторного ревью: `github.recent_issues(repo)` тоже ловится
    поштучно, на уровне ВНЕШНЕГО цикла `for repo in repos`. Раньше отказ
    здесь не ловился и уходил наверх — отказ одного репозитория обрывал
    обход целиком, и репозитории после сломанного не обрабатывались вовсе,
    ни в этом заходе, ни в последующих, пока первый не починится. Это не
    гипотетический риск, а буквальное повторение беды обслуживаемого
    контура (171 падение подряд из-за одного недоступного репозитория) —
    именно поэтому её нельзя было оставлять непойманной здесь.

    У отказа репозитория нет номера конкретной задачи — недоступен весь
    репозиторий, ни одна задача из него не разобрана. В `problems`
    записываем 0: настоящие номера задач GitHub всегда положительные,
    поэтому 0 однозначно читается как «это не про конкретную задачу», а имя
    репозитория остаётся в тексте причины — сведения о том, что именно
    отказало, не теряются.
    """
    made = 0
    for repo in repos:
        try:
            issues = github.recent_issues(repo)
        except Exception as error:
            if problems is not None:
                problems.append((
                    0,
                    f"репозиторий {repo}: чтение последних задач не удалось: {error}",
                ))
            continue
        for issue in issues:
            number = int(issue["number"])
            key = f"gh:{repo}:{number}:{issue['updated']}"
            if mapping.seen(key):
                continue

            mapping.mark_seen(key)
            try:
                parent = mapping.thread_for(repo, number)
                if parent is None:
                    text = (
                        f"{issue['title']}\n\n"
                        f"{repo.split('/')[-1]} · #{number}  {TAGS['event']}"
                    )
                    status_id = feed.post("issue_agent", text)
                    mapping.remember_thread(repo, number, status_id)
                else:
                    feed.post(
                        "issue_agent",
                        f"Состояние задачи: {issue['state']}.",
                        in_reply_to=parent,
                    )
            except Exception as error:
                mapping.forget_seen(key)
                if problems is not None:
                    problems.append((
                        number,
                        f"событие GitHub {repo}#{number}: публикация не удалась: {error}",
                    ))
                continue
            made += 1
    return made


def pump_cleanup(
    github,
    read_touched,
    mark_cleaned=None,
    problems: list[tuple[int, str]] | None = None,
) -> int:
    """Контур метку решения не снимает никогда. Пока она висит, повторная
    постановка той же метки не породит события — и следующая развилка той же
    фазы не сработает. Убираем, как только ожидание закончилось.

    Итоговый обзор, правка №1 (критично): раньше `mark_cleaned` звался
    БЕЗУСЛОВНО — в том же проходе, где `pump_votes` только что поставил
    метку решения. В этот момент обслуживаемая система ещё не сняла
    `needs-human:triage` (ей нужно время добраться до этой метки),
    `stale_decision_labels` честно возвращает пустой список (задача ведь
    ЕЩЁ ждёт человека), но задача уже помечалась убранной и в выборку целей
    больше не попадала. Метка решения оставалась навсегда, а вместе с ней —
    невозможность повторной развилки той же фазы (см. правку №3).

    Задача считается убранной, только когда она ДЕЙСТВИТЕЛЬНО перестала
    ждать человека (`not is_parked(labels)`) — не когда `stale_decision_labels`
    просто ничего не нашла: эти две причины пустого списка разные
    («ожидание ещё держится» и «снимать было нечего») и должны вести к
    разным решениям. Метки читаем ОДИН раз на (repo, issue), а не дважды
    (для снятия и для проверки), — see итоговый обзор, правка №5.

    Пока ожидание держится, `mark_cleaned` не зовётся вовсе: цель остаётся
    в `decided_uncleaned()` и уборка вернётся к ней на следующем обходе.
    Как только (repo, issue) действительно перестали ждать — зовём
    `mark_cleaned`, если он передан: без этого мост навсегда дёргал бы
    GitHub по каждой когда-либо решённой задаче (см. `Mapping.mark_cleaned`
    / `decided_uncleaned`). `Mapping.mark_cleaned` заодно снимает ключ
    развилки этой фазы (правка №3) — цикл «развилка → голос → уборка»
    закрыт, следующий может начаться.

    Отказ по одной задаче не должен останавливать уборку остальных.
    """
    removed = 0
    for repo, issue in read_touched():
        try:
            labels = github.labels(repo, issue)
            for label in stale_decision_labels(labels):
                github.remove_label(repo, issue, label)
                removed += 1
            if mark_cleaned is not None and not is_parked(labels):
                mark_cleaned(repo, issue)
        except Exception as error:
            if problems is not None:
                problems.append((issue, str(error)))
    return removed


def publish_report(
    feed, mapping: Mapping, repo: str, issue: int, *,
    passed: int, total: int, seconds: int, blocked: list[str], shots: list[Path],
) -> tuple[str, str | None]:
    """Отчёт приёмки: улики вложениями. Вложение и опрос в одном посте Mastodon
    не принимает, поэтому продолжение уходит ОТДЕЛЬНЫМ постом-ответом.

    Продолжение — обычный ответ, не опрос: варианты «принять / перепрогнать»
    не отображаются ни в одну метку контура, голос по ним ничего бы не сделал.
    Кнопка, которая ничего не делает, хуже её отсутствия.

    Вызывается вручную агентом приёмки, а не циклом перекачки — поштучная
    обработка отказов здесь не нужна (нет соседних задач, которые отказ одной
    не должен топить, как в pump_*). Отказ публикации САМОГО отчёта уходит
    наверх как есть: `feed.post_media` уже заворачивает причину в
    `RuntimeError`, а до первого поста мост ничего не публиковал — терять
    нечего.

    Отказ ПРОДОЛЖЕНИЯ — другое дело: к этому моменту отчёт уже опубликован
    и виден в ленте (`report_id`). Голый отказ `feed.post` его id не несёт,
    и разбирающий поломку узнал бы о том, что отчёт всё же дошёл, только
    сверкой вручную. Поэтому отказ продолжения перезаворачивается: id
    опубликованного отчёта — в тексте, исходная причина — через
    `raise ... from error`, а не потеряна."""
    parent = mapping.thread_for(repo, issue)
    text = render_report(passed=passed, total=total, seconds=seconds, blocked=blocked)
    report_id = feed.post_media("howtodemo", text, shots, in_reply_to=parent)
    if passed == total and not blocked:
        return report_id, None
    unproven = " ".join(unaccounted_sentences(passed, total, blocked))
    try:
        follow_id = feed.post(
            "howtodemo",
            render_agent_reply(
                body=f"{unproven} Решение за тобой.",
                reason="вердикт посчитан кодом, недостающие шаги названы поимённо",
            ),
            in_reply_to=report_id,
        )
    except Exception as error:
        raise RuntimeError(
            f"отчёт {report_id} опубликован, а продолжение к нему — нет: {error}"
        ) from error
    return report_id, follow_id
