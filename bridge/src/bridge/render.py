from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    """Вариант ответа: что читает человек и какая метка уходит в контур."""

    title: str
    label: str
