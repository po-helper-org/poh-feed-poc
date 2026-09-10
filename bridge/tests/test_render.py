import pytest

from bridge.render import (
    TAGS,
    POLL_LIFETIME_SECONDS, question_for_phase, render_decision,
    render_report, render_agent_reply,
)

REPO = "po-helper-org/poh-demo-checkout"


def test_classified_offers_research_and_bug():
    question, choices = question_for_phase("classified")
    assert [c.label for c in choices] == ["research-me", "bug-me"]
    assert question


def test_ready_for_dev_offers_build():
    _, choices = question_for_phase("ready-for-dev")
    assert [c.label for c in choices] == ["build-me"]


def test_duplicate_offers_both_answers():
    _, choices = question_for_phase("duplicate")
    assert sorted(c.label for c in choices) == ["confirm-duplicate", "not-duplicate"]


def test_unknown_phase_has_no_question():
    assert question_for_phase("in-development") is None


def test_decision_carries_coordinates_and_titles():
    question, choices = question_for_phase("classified")
    post = render_decision(repo=REPO, issue=149, question=question, choices=choices)
    assert "poh-demo-checkout · #149" in post.text
    assert post.options == [c.title for c in choices]
    assert post.expires_in == POLL_LIFETIME_SECONDS


def test_decision_warns_that_bug_path_is_not_implemented():
    """Контур на bug-me падает NotImplementedError. Лента обязана сказать
    об этом до нажатия, а не после."""
    question, choices = question_for_phase("classified")
    post = render_decision(repo=REPO, issue=149, question=question, choices=choices)
    assert "не реализован" in post.text


def test_decision_does_not_promise_a_default():
    """Контур по таймауту НЕ применяет вариант, а снимает задачу с ожидания.
    Обещать дефолт значит врать о поведении системы."""
    question, choices = question_for_phase("classified")
    post = render_decision(repo=REPO, issue=149, question=question, choices=choices)
    assert "возьму" not in post.text
    assert "эскалац" in post.text


def test_decision_rejects_empty_choices():
    with pytest.raises(ValueError, match="вариант"):
        render_decision(repo=REPO, issue=1, question="q", choices=[])


# --- Итоговый обзор, правка №6: текст не должен врать о сроке опроса -------


def test_decision_states_the_real_poll_deadline_honestly():
    """Раньше текст утверждал «у развилки срока нет», а `Feed.post_poll`
    тихо подставлял 7 суток от себя (API ленты требует `expires_in`
    обязательным полем — технически бессрочного опроса не бывает). Через
    неделю без ответа развилка молча переставала голосоваться. Текст обязан
    называть срок опроса честно, отдельно от того, что у самого РЕШЕНИЯ
    контура срока нет (это по-прежнему правда и по-прежнему в тексте)."""
    question, choices = question_for_phase("classified")
    post = render_decision(repo=REPO, issue=149, question=question, choices=choices)
    assert "7 суток" in post.text
    assert "эскалац" in post.text, "срок решения контура остаётся назван"


def test_report_states_blocked_steps_in_words():
    text = render_report(passed=4, total=5, seconds=96, blocked=["шаг 4 — браузер"])
    assert "4 из 5" in text and "96 с" in text
    assert "шаг 4 — браузер" in text and "Не проверял" in text


def test_report_without_blocked_says_nothing_about_them():
    assert "Не проверял" not in render_report(passed=5, total=5, seconds=96, blocked=[])


def test_report_explains_failed_steps_even_without_blocked():
    """passed < total при пустом blocked — расхождение не объяснить одним
    молчанием: пятый шаг не «не проверялся», он провалился, и об этом
    обязаны сказать словами, раз имён у функции нет."""
    text = render_report(passed=4, total=5, seconds=96, blocked=[])
    assert "4 из 5" in text
    assert "Не проверял" not in text, "провалившийся шаг — не то же самое, что непроверенный"
    assert "1" in text
    assert "имена не переданы" in text


def test_report_separates_failed_from_blocked():
    """passed < total при непустом blocked, где часть шагов провалилась, а
    часть не проверялась вовсе — оба явления обязаны быть названы порознь,
    одним словом их объединять нельзя."""
    text = render_report(passed=3, total=6, seconds=40, blocked=["шаг 2 — сеть"])
    assert "Не проверял: шаг 2 — сеть" in text
    assert "имена не переданы" in text
    assert "2" in text


def test_report_rejects_inconsistent_arithmetic():
    """passed + len(blocked) > total — вызывающий передал чушь, молча
    принимать её нельзя."""
    with pytest.raises(ValueError):
        render_report(passed=5, total=5, seconds=96, blocked=["шаг 6 — призрак"])


def test_agent_reply_requires_reason():
    text = render_agent_reply(body="Беру в работу", reason="подписка на приёмку")
    assert "Беру в работу" in text and "почему: подписка на приёмку" in text
    with pytest.raises(ValueError):
        render_agent_reply(body="Беру", reason="")


def test_decision_carries_its_event_tag():
    """Тег типа события — опора панели шорткатов: список Mastodon это набор
    аккаунтов, а «ждут решения» — срез по смыслу, выразимый только тегом."""
    question, choices = question_for_phase("classified")
    post = render_decision(repo=REPO, issue=149, question=question, choices=choices)
    assert TAGS["decision"] in post.text


def test_report_carries_its_event_tag():
    assert TAGS["report"] in render_report(passed=5, total=5, seconds=96, blocked=[])


def test_event_tags_are_not_purely_numeric():
    """Чисто цифровой хэштег сервер тегом не считает — номер задачи в тег не
    превратится, и это защищает от ложных срезов вроде «#149»."""
    for tag in TAGS.values():
        assert tag.startswith("#")
        assert not tag[1:].isdigit()
