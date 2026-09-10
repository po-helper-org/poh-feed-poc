#!/usr/bin/env bash
# Подписывает человека на учётки-источники и агентов.
#
# Зачем отдельным шагом: домашняя лента показывает только тех, на кого ты
# подписан. Без подписок она пуста, и это не поломка, а устройство — но
# выглядит как поломка, поэтому шаг обязателен при первой настройке.
#
# Запускает ЧЕЛОВЕК: скрипт входит под личной учёткой, а её пароль не место
# ни в чужих руках, ни в автоматике.
set -euo pipefail
BASE=${FEED_URL:-http://127.0.0.1:8080}
ME=${FEED_HUMAN:-aleks}

if [ -z "${FEED_ACCOUNT_PASSWORD:-}" ]; then
  echo "ОШИБКА: не задан FEED_ACCOUNT_PASSWORD." >&2
  echo "Запустите так:  FEED_ACCOUNT_PASSWORD='...' ./scripts/follow_sources.sh" >&2
  exit 1
fi

SOURCES=("$@")
if [ ${#SOURCES[@]} -eq 0 ]; then
  SOURCES=(product_radar problemhunt crm_harness tg_aleks
           issue_agent openhands pr_agent howtodemo delivery harness)
fi

echo "== Вход под $ME =="
APP=$(curl -sS -X POST "$BASE/api/v1/apps" \
  -d client_name=follow-sources \
  -d redirect_uris=urn:ietf:wg:oauth:2.0:oob \
  -d scopes='read write')
CID=$(echo "$APP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["client_id"])')
CSEC=$(echo "$APP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["client_secret"])')
AUTH="$BASE/oauth/authorize?client_id=$CID&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code&scope=read+write"

cookie() { grep -i '^set-cookie' | head -1 | sed -E 's/^[Ss]et-[Cc]ookie: ([^;]+);.*/\1/' | tr -d '\r'; }

C1=$(curl -sS -D - -o /dev/null "$AUTH" | cookie)
C2=$(curl -sS -D - -o /dev/null -X POST "$BASE/auth/sign_in" -H "Cookie: $C1" \
  --data-urlencode "username=${ME}@feed.local" \
  --data-urlencode "password=${FEED_ACCOUNT_PASSWORD}" | cookie)
if [ -z "$C2" ]; then
  echo "ОШИБКА: вход под $ME не дал сессии — проверьте пароль." >&2
  exit 1
fi
curl -sS -o /dev/null "$AUTH" -H "Cookie: $C2"
CODE=$(curl -sS -D - -o /dev/null -X POST "$BASE/oauth/authorize" -H "Cookie: $C2" \
  | grep -i '^location' | sed -E 's/.*code=([A-Za-z0-9_-]+).*/\1/' | tr -d '\r')
TOKEN=$(curl -sS -X POST "$BASE/oauth/token" \
  -d grant_type=authorization_code -d code="$CODE" \
  -d client_id="$CID" -d client_secret="$CSEC" \
  -d redirect_uri='urn:ietf:wg:oauth:2.0:oob' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

echo "== Подписки =="
for u in "${SOURCES[@]}"; do
  ID=$(curl -sS "$BASE/api/v1/accounts/lookup?acct=$u" -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import sys,json
try: print(json.load(sys.stdin)["id"])
except Exception: print("")')
  if [ -z "$ID" ]; then
    echo "  пропущен $u — такой учётки нет"
    continue
  fi
  STATE=$(curl -sS -X POST "$BASE/api/v1/accounts/$ID/follow" \
    -H "Authorization: Bearer $TOKEN" -d reblogs=true \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print("подписан" if d.get("following") else ("ждёт одобрения" if d.get("requested") else "не вышло"))')
  printf '  %-16s %s\n' "$u" "$STATE"
done

echo
echo "Готово. Домашняя лента: http://127.0.0.1:8083/#/"
