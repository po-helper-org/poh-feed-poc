from bridge.harness import Parked, phase_of, is_parked, stale_decision_labels, parked_from


def test_phase_read_from_label():
    assert phase_of(["phase:classified", "priority:P3"]) == "classified"


def test_phase_absent_gives_none():
    assert phase_of(["priority:P3"]) is None


def test_parked_detected_by_single_label():
    assert is_parked(["needs-human:triage", "phase:classified"]) is True
    assert is_parked(["phase:classified"]) is False


def test_parked_from_keeps_only_waiting_issues():
    issues = [
        {"repo": "o/r", "number": 149, "title": "Промокод",
         "labels": ["needs-human:triage", "phase:classified"]},
        {"repo": "o/r", "number": 151, "title": "Другое",
         "labels": ["phase:in-development"]},
    ]
    assert parked_from(issues) == [
        Parked(repo="o/r", issue=149, title="Промокод", phase="classified")
    ]


def test_parked_without_phase_label_is_skipped():
    """Без фазы неизвестно, какие метки решения допустимы, а метка не из своей
    фазы игнорируется контуром МОЛЧА. Лучше не предлагать ничего."""
    issues = [{"repo": "o/r", "number": 7, "title": "т",
               "labels": ["needs-human:triage"]}]
    assert parked_from(issues) == []


def test_stale_decision_labels_only_when_no_longer_waiting():
    assert stale_decision_labels(["research-me", "phase:business-analysis"]) == ["research-me"]
    assert stale_decision_labels(["research-me", "needs-human:triage"]) == []
    assert stale_decision_labels(["phase:merged"]) == []


def test_stale_decision_labels_returns_all_of_them():
    got = stale_decision_labels(["research-me", "build-me", "phase:merged"])
    assert sorted(got) == ["build-me", "research-me"]
