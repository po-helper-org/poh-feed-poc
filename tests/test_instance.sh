#!/usr/bin/env bash
# Проверка стенда ленты. Три утверждения, каждое обязательно.
set -u
fail=0

echo "== 1. Инстанс отвечает =="
if curl -fsS http://127.0.0.1:8080/api/v1/instance | grep -q '"uri"'; then
  echo "OK"
else
  echo "ПРОВАЛ: инстанс не отвечает"; fail=1
fi

echo "== 2. Наружу не смотрит ни один порт =="
# Публикация считается наружной, если адрес не 127.0.0.1: это и 0.0.0.0,
# и конкретный адрес интерфейса, и IPv6-вариант.
outside=$(docker compose ps --format '{{.Ports}}' | tr ',' '\n' \
  | grep -E -- '->' | grep -v -- '^ *127\.0\.0\.1:' || true)
if [ -z "$outside" ]; then
  echo "OK"
else
  echo "ПРОВАЛ: порты опубликованы не только на 127.0.0.1:"; echo "$outside"; fail=1
fi

echo "== 3. Федерация закрыта =="
code=$(curl -s -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:8080/api/v2/search?q=https://mastodon.social/@Gargron&resolve=true")
if [ "$code" = "401" ] || [ "$code" = "403" ] || [ "$code" = "422" ]; then
  echo "OK (поиск чужого домена отвергнут, код $code)"
elif [ "$code" = "200" ]; then
  echo "ПРОВАЛ: инстанс попытался разрешить внешний адрес"; fail=1
else
  echo "OK (код $code, внешнее разрешение недоступно)"
fi

exit $fail
