"""Судья для промт-правил: модель через OpenAI-совместимый чат, ответ — JSON.

Без SDK, на stdlib: скрипты переноса запускаются системным python без
окружения моста, и лишняя зависимость сломала бы их первыми.

Поставщик задаётся окружением, а не кодом: контур ходит в GLM через z.ai,
и здесь нужен тот же адрес, что уже оплачен.

    LABELS_LLM_BASE_URL   например https://api.z.ai/api/paas/v4
    LABELS_LLM_API_KEY
    LABELS_LLM_MODEL      например glm-5.2

Ответы запоминаются в файле рядом с файлом меток по хэшу (учётка, текст,
промты): перемаркировка сотни постов не задаёт сто раз один и тот же вопрос,
а смена промта — новый хэш — спрашивает заново сама.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

SYSTEM = (
    "Ты размечаешь посты рабочей ленты метками. Тебе дают текст поста, автора "
    "и список меток с описанием, когда метка подходит. Верни ТОЛЬКО JSON вида "
    '{"labels": ["id", ...]} — идентификаторы подошедших меток из списка, '
    "пустой список, если ни одна не подходит. Никакого другого текста."
)


class JudgeError(RuntimeError):
    pass


class LlmJudge:
    def __init__(self, base_url: str, api_key: str, model: str, cache_file: Path | None = None):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._model = model
        self._cache_file = cache_file
        self._cache: dict[str, list[str]] = {}
        if cache_file and cache_file.exists():
            self._cache = json.loads(cache_file.read_text(encoding="utf-8"))
        self.asked = 0  # сколько раз ходили в модель — чтобы прогон мог это назвать

    @staticmethod
    def from_env(cache_file: Path | None = None) -> "LlmJudge | None":
        url, key, model = (os.environ.get(n, "") for n in
                           ("LABELS_LLM_BASE_URL", "LABELS_LLM_API_KEY", "LABELS_LLM_MODEL"))
        if not (url and key and model):
            return None
        return LlmJudge(url, key, model, cache_file)

    def __call__(self, acct: str, text: str, candidates: dict[str, str]) -> list[str]:
        key = hashlib.sha256(json.dumps([acct, text, candidates], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in self._cache:
            return list(self._cache[key])
        verdict = self._ask(acct, text, candidates)
        self._cache[key] = verdict
        if self._cache_file:
            self._cache_file.write_text(json.dumps(self._cache, ensure_ascii=False, indent=0), encoding="utf-8")
        return list(verdict)

    def _ask(self, acct: str, text: str, candidates: dict[str, str]) -> list[str]:
        listing = "\n".join(f"- {label_id}: {prompt}" for label_id, prompt in candidates.items())
        user = f"Автор: @{acct}\n\nПост:\n{text[:4000]}\n\nМетки:\n{listing}"
        payload = {
            "model": self._model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        }
        req = urllib.request.Request(
            self._url, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
        )
        self.asked += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                answer = json.load(r)["choices"][0]["message"]["content"]
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            raise JudgeError(f"модель не ответила: {error}") from error
        return parse_verdict(answer, candidates)


def parse_verdict(answer: str, candidates: dict[str, str]) -> list[str]:
    """Ответ модели — JSON, возможно обёрнутый в ```-блок. Всё, что не
    разбирается, — ошибка вслух, а не «ни одна не подошла»."""
    raw = answer.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    try:
        data = json.loads(raw)
        labels = data["labels"]
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise JudgeError(f"ответ модели не разбирается: {answer[:200]!r}") from error
    return [x for x in labels if x in candidates] if set(labels) <= set(candidates) else labels
