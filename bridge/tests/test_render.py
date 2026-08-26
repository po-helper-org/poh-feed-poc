import pytest

from bridge.render import (
    Choice, question_for_phase, render_decision, render_artifact,
    render_report, render_incident, render_agent_reply,
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
    assert post.expires_in is None


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


def test_artifact_splits_into_spoiler_and_body():
    spoiler, body = render_artifact(
        title="БФТ · промокод из ссылки", words=4210, body_md="## 01 Границы\n\nтекст",
    )
    assert spoiler == "БФТ · промокод из ссылки · 4210 слов"
    assert body.startswith("## 01 Границы")


def test_report_states_blocked_steps_in_words():
    text = render_report(passed=4, total=5, seconds=96, blocked=["шаг 4 — браузер"])
    assert "4 из 5" in text and "96 с" in text
    assert "шаг 4 — браузер" in text and "Не проверял" in text


def test_report_without_blocked_says_nothing_about_them():
    assert "Не проверял" not in render_report(passed=5, total=5, seconds=96, blocked=[])


def test_incident_carries_evidence():
    text = render_incident(
        what="Стенд перепинован чужой сессией",
        evidence="ISSUE_AGENT_CONTEXT уехал на a71f0c2",
    )
    assert "Стенд перепинован" in text and "a71f0c2" in text


def test_agent_reply_requires_reason():
    text = render_agent_reply(body="Беру в работу", reason="подписка на приёмку")
    assert "Беру в работу" in text and "почему: подписка на приёмку" in text
    with pytest.raises(ValueError):
        render_agent_reply(body="Беру", reason="")
