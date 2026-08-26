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
set -euo pipefail
BASE=http://127.0.0.1:8080
PASS='PoC-feed-2026!'
ENV_FILE=.env

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

> "$ENV_FILE.tmp"

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

  # 5) обмен кода на токен
  TOKRESP=$(curl -sS -X POST "$BASE/oauth/token" \
    -d grant_type=authorization_code -d code="$CODE" \
    -d client_id="$CID" -d client_secret="$CSEC" \
    -d redirect_uri='urn:ietf:wg:oauth:2.0:oob')
  TOK=$(echo "$TOKRESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
  VAR="FEED_TOKEN_$(echo "$a" | tr 'a-z' 'A-Z')"
  echo "${VAR}=${TOK}" >> "$ENV_FILE.tmp"
  echo "OK: $VAR получен"
done

mv "$ENV_FILE.tmp" "$ENV_FILE"
echo "== Готово =="
cat "$ENV_FILE"
