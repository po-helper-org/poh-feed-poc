import re

import pytest

from bridge.labels import (
    hashtags_in,
    labels_for,
    parse_labels,
    strip_label_tags,
    with_labels,
)


def labels(*items):
    return parse_labels({"labels": list(items)})


def test_label_applies_when_any_rule_matches():
    ls = labels({"id": "разработка", "title": "Разработка",
                 "rules": [{"account": "openhands"}, {"tag": "контур_задача"}]})
    assert labels_for(ls, "openhands", "починил тест") == ["разработка"]
    assert labels_for(ls, "product_radar", "новая задача #контур_задача") == ["разработка"]
    assert labels_for(ls, "product_radar", "просто пост") == []


def test_contains_and_regex_ignore_case_and_account_domain():
    ls = labels({"id": "релизы", "rules": [{"contains": "Релиз"}]},
                {"id": "gds", "rules": [{"regex": r"\bGDS\b"}]},
                {"id": "клиенты", "rules": [{"account": "@tg_aleks"}]})
    assert labels_for(ls, "delivery", "РЕЛИЗ 1.2 выпущен") == ["релизы"]
    assert labels_for(ls, "delivery", "интеграция с gds готова") == ["gds"]
    assert labels_for(ls, "delivery", "gdsx не считается") == []
    assert labels_for(ls, "tg_aleks@feed.localtest.me", "привет") == ["клиенты"]


def test_order_follows_declaration_not_match():
    ls = labels({"id": "b", "rules": [{"contains": "x"}]},
                {"id": "a", "rules": [{"contains": "x"}]})
    assert labels_for(ls, "u", "x") == ["b", "a"]


@pytest.mark.parametrize("bad", [
    {"labels": [{"id": "с пробелом", "rules": []}]},
    {"labels": [{"id": "123", "rules": []}]},
    {"labels": [{"id": "a", "rules": []}, {"id": "A", "rules": []}]},
    {"labels": [{"id": "a", "rules": [{"contains": "x", "tag": "y"}]}]},
    {"labels": [{"id": "a", "rules": [{"nope": "x"}]}]},
    {"labels": [{"id": "a", "rules": [{"contains": "   "}]}]},
    {"labels": [{"id": "a", "rules": [{"regex": "("}]}]},
    {"nope": []},
])
def test_broken_file_fails_loudly_at_load(bad):
    """Сломанное правило обязано ломаться при загрузке, а не оставлять посты
    без метки молча на первой публикации."""
    with pytest.raises(ValueError):
        parse_labels(bad)


def test_with_labels_appends_once_and_keeps_system_tag_line():
    ls = labels({"id": "разработка", "rules": [{"account": "issue_agent"}]})
    text = "Задача классифицирована.\n\npoh-demo-checkout · #159  #контур_развилка"
    once = with_labels(ls, "issue_agent", text)
    assert once.endswith("#контур_развилка #разработка")
    assert with_labels(ls, "issue_agent", once) == once


def test_with_labels_starts_new_paragraph_when_no_tag_line():
    ls = labels({"id": "продукт", "rules": [{"account": "product_radar"}]})
    assert with_labels(ls, "product_radar", "Запуск сервиса.") == "Запуск сервиса.\n\n#продукт"


def test_with_labels_leaves_text_alone_when_nothing_matches():
    ls = labels({"id": "продукт", "rules": [{"account": "product_radar"}]})
    assert with_labels(ls, "openhands", "текст") == "текст"


def test_strip_label_tags_removes_only_labels():
    text = "Текст.\n\npoh · #159  #контур_развилка #разработка #клиенты"
    out = strip_label_tags(text, {"разработка", "клиенты"})
    assert out == "Текст.\n\npoh · #159  #контур_развилка"
    assert hashtags_in(out) == {"159", "контур_развилка"}
