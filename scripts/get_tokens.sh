#!/usr/bin/env bash
# Получает пользовательские токены доступа для агентов.
#
# ВАЖНО: GoToSocial НЕ поддерживает grant_type=password (проверено —
# сервер отвечает unsupported_grant_type, поддерживаются только
# authorization_code и client_credentials). Это не расширение Mastodon
# API, а сознательное ограничение GoToSocial.
#
# Поэтому здесь честно пройден документированный веб-флоу
# authorization_code теми же HTTP-запросами, что делает браузер:
#   1) GET  /oauth/authorize      -> сессионная кука (неавторизован)
#   2) POST /auth/sign_in         -> логин по этой куке, новая (авторизованная) кука
#   3) GET  /oauth/authorize      -> страница согласия приложения
#   4) POST /oauth/authorize      -> редирект с кодом (?code=...)
#   5) POST /oauth/token          -> обмен кода на access_token
#
# Domain-скоуп куки (feed.localhost) — это соглашение браузерных
# cookie jar, а не то, что проверяет сам сервер: он смотрит на значение
# куки. Поэтому кука пересылается вручную заголовком Cookie напрямую на
# http://127.0.0.1:8080, без похода через Caddy/TLS.
#
# .env сливается, а не перезаписывается целиком: строки FEED_TOKEN_*
# обновляются, всё остальное содержимое .env (например, будущий
# GITHUB_TOKEN) сохраняется как есть. Если для какого-то агента токен
# получить не удалось, прежнее значение его переменной в .env не трогается.
set -euo pipefail
BASE=http://127.0.0.1:8080
ENV_FILE=.env

# Существующие на этом стенде аккаунты заведены с паролем из FEED_ACCOUNT_PASSWORD —
# подставляйте его, если не меняли сознательно.
if [ -z "${FEED_ACCOUNT_PASSWORD:-}" ]; then
  echo "ОШИБКА: переменная окружения FEED_ACCOUNT_PASSWORD не задана." >&2
  echo "Задайте её и запустите скрипт снова, например:" >&2
  echo "  FEED_ACCOUNT_PASSWORD='...' ./scripts/get_tokens.sh" >&2
  exit 1
fi
PASS="$FEED_ACCOUNT_PASSWORD"

NEW_TOKENS_FILE=$(mktemp)
trap 'rm -f "$NEW_TOKENS_FILE"' EXIT

extract_cookie() {
  # Достаёт "имя=значение" из строки Set-Cookie (без учёта Domain/Secure/etc).
  grep -i '^set-cookie' | head -1 | sed -E 's/^[Ss]et-[Cc]ookie: ([^;]+);.*/\1/' | tr -d '\r'
}

echo "== Регистрация приложения моста =="
APP=$(curl -sS -X POST "$BASE/api/v1/apps" \
  -d client_name=poh-bridge \
  -d redirect_uris=urn:ietf:wg:oauth:2.0:oob \
  -d scopes='read write')
CID=$(echo "$APP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["client_id"])')
CSEC=$(echo "$APP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["client_secret"])')
echo "client_id=$CID"

OBTAINED=""

for a in issue_agent openhands pr_agent howtodemo delivery harness; do
  echo "== Токен для $a =="

  # 1) неавторизованная сессия
  H1=$(curl -sS -D - -o /dev/null "$BASE/oauth/authorize?client_id=$CID&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code&scope=read+write")
  COOKIE1=$(echo "$H1" | extract_cookie)

  # 2) логин формой /auth/sign_in
  H2=$(curl -sS -D - -o /dev/null -X POST "$BASE/auth/sign_in" \
    -H "Cookie: $COOKIE1" \
    --data-urlencode "username=${a}@feed.local" \
    --data-urlencode "password=${PASS}")
  COOKIE2=$(echo "$H2" | extract_cookie)
  if [ -z "$COOKIE2" ]; then
    echo "ОШИБКА: логин для $a не дал новой сессии, пропускаю" >&2
    continue
  fi

  # 3) страница согласия (устанавливает pending-запрос в сессии)
  curl -sS -o /dev/null "$BASE/oauth/authorize?client_id=$CID&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code&scope=read+write" \
    -H "Cookie: $COOKIE2"

  # 4) подтверждение — получаем код в Location
  H4=$(curl -sS -D - -o /dev/null -X POST "$BASE/oauth/authorize" -H "Cookie: $COOKIE2")
  CODE=$(echo "$H4" | grep -i '^location' | sed -E 's/.*code=([A-Za-z0-9_-]+).*/\1/' | tr -d '\r')
  if [ -z "$CODE" ]; then
    echo "ОШИБКА: код авторизации для $a не получен" >&2
    continue
  fi

  # 5) обмен кода на токен. Отказ (invalid_grant и т.п.) должен обрывать
  # только эту итерацию, а не весь скрипт — поэтому команда-подстановка
  # стоит в условии if, а не в самостоятельном присваивании (под
  # set -e самостоятельное VAR=$(cmd) с ненулевым кодом завершило бы
  # весь скрипт).
  TOKRESP=$(curl -sS -X POST "$BASE/oauth/token" \
    -d grant_type=authorization_code -d code="$CODE" \
    -d client_id="$CID" -d client_secret="$CSEC" \
    -d redirect_uri='urn:ietf:wg:oauth:2.0:oob')
  if ! TOK=$(echo "$TOKRESP" | python3 -c 'import sys,json
print(json.load(sys.stdin)["access_token"])' 2>/dev/null); then
    echo "ОШИБКА: обмен кода на токен для $a не удался. Ответ сервера: $TOKRESP" >&2
    continue
  fi

  VAR="FEED_TOKEN_$(echo "$a" | tr 'a-z' 'A-Z')"
  echo "${VAR}=${TOK}" >> "$NEW_TOKENS_FILE"
  OBTAINED="$OBTAINED $VAR"
  echo "OK: $VAR получен"
done

# Слияние: строки FEED_TOKEN_* из NEW_TOKENS_FILE обновляют одноимённые
# строки в существующем .env (если есть), остальное содержимое .env
# сохраняется как есть; переменные, которых раньше не было, дописываются
# в конец. Если .env ещё не существует, создаётся заново только из
# полученных токенов.
python3 - "$ENV_FILE" "$NEW_TOKENS_FILE" <<'PY'
import sys

env_file, new_file = sys.argv[1], sys.argv[2]

def var_name(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None
    return line.split("=", 1)[0]

new_vars = {}
with open(new_file) as f:
    for line in f:
        line = line.rstrip("\n")
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        new_vars[k] = v

try:
    with open(env_file) as f:
        existing_lines = f.readlines()
except FileNotFoundError:
    existing_lines = []

consumed = set()
out_lines = []
for line in existing_lines:
    if not line.endswith("\n"):
        line += "\n"
    name = var_name(line)
    if name is not None and name in new_vars:
        out_lines.append(f"{name}={new_vars[name]}\n")
        consumed.add(name)
    else:
        out_lines.append(line)

for name, value in new_vars.items():
    if name not in consumed:
        out_lines.append(f"{name}={value}\n")

with open(env_file + ".tmp", "w") as f:
    f.writelines(out_lines)
PY
mv "$ENV_FILE.tmp" "$ENV_FILE"

echo "== Готово =="
if [ -n "$OBTAINED" ]; then
  echo "Обновлены переменные:$OBTAINED"
else
  echo "Ни один токен не получен — .env не изменён по составу переменных."
fi
