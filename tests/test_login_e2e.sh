#!/usr/bin/env bash
# Сквозная проверка входа в ленту через Phanpy.
#
# Проверяет всю цепочку до формы ввода пароля. Сам ввод пароля не
# автоматизируется намеренно: это шаг человека.
#
# Каждое утверждение здесь появилось из живой поломки, а не из головы.
# Порядок проверок соответствует порядку, в котором клиент упирался.
set -u
fail=0
ok()   { echo "  OK   $1"; }
bad()  { echo "  ПРОВАЛ  $1"; fail=1; }

echo "== 1. Контейнеры подняты =="
for c in poh-feed poh-feed-tls poh-feed-client; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "true" ]; then
    ok "$c работает"
  else
    bad "$c не работает — поднимите: docker compose up -d"
  fi
done

echo "== 2. Ни одного порта наружу =="
outside=$(docker compose ps --format '{{.Ports}}' 2>/dev/null | tr ',' '\n' \
  | grep -E '\->' | grep -v '^ *127\.0\.0\.1:' || true)
if [ -z "$outside" ]; then ok "все публикации на 127.0.0.1"
else bad "порты опубликованы не только на петле: $outside"; fi

echo "== 3. Инстанс на стандартном порту 443 =="
# Phanpy не принимает порт в поле адреса сервера: с «localhost:8443»
# кнопка входа остаётся неактивной и запрос не уходит вовсе.
code=$(curl -s -o /dev/null -w '%{http_code}' https://feed.localtest.me/api/v1/instance)
if [ "$code" = "200" ]; then ok "https://feed.localtest.me отвечает 200"
else bad "https://feed.localtest.me отвечает $code — TLS на 443 не поднят"; fi

echo "== 4. Имя резолвится без правки /etc/hosts =="
# Phanpy требует домен С ТОЧКОЙ: «localhost» он доменом не считает и молча
# подставляет чужую подсказку из своего списка серверов.
ip=$(ping -c1 -t2 feed.localtest.me 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ "$ip" = "127.0.0.1" ]; then ok "feed.localtest.me -> 127.0.0.1"
else bad "feed.localtest.me резолвится в '$ip', ожидалось 127.0.0.1"; fi

echo "== 5. Сертификат доверен системой =="
# Кука сессии выдаётся с флагом Secure. По недоверенному соединению
# браузер её выбрасывает, и вход молча не срабатывает.
if curl -sf https://feed.localtest.me/api/v1/instance >/dev/null 2>&1; then
  ok "TLS принимается без --insecure"
else
  bad "сертификат не доверен — доверьте корневой: sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain caddy-root.crt"
fi

echo "== 6. Регистрация приложения проходит =="
# Это ровно тот запрос, на котором клиент падал с ERR_SSL_PROTOCOL_ERROR,
# когда инстанс отдавался по простому HTTP.
app=$(curl -sf -X POST https://feed.localtest.me/api/v1/apps \
  -d client_name=e2e-login-check \
  -d redirect_uris=urn:ietf:wg:oauth:2.0:oob \
  -d scopes='read write' 2>/dev/null)
cid=$(echo "$app" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("client_id",""))' 2>/dev/null)
if [ -n "$cid" ]; then ok "приложение зарегистрировано, client_id получен"
else bad "регистрация приложения не прошла: $app"; fi

echo "== 7. Страница входа отдаётся =="
# Дальше человек вводит почту и пароль. Этот шаг не автоматизируется.
if [ -n "$cid" ]; then
  # Сервер отвечает 303 и уводит на /auth/sign_in — идём за переадресацией,
  # иначе проверка считает пустое тело отсутствием формы.
  body=$(curl -sfL "https://feed.localtest.me/oauth/authorize?client_id=$cid&redirect_uri=urn:ietf:wg:oauth:2.0:oob&response_type=code&scope=read+write")
  if echo "$body" | grep -qi "password"; then ok "форма входа отдана"
  else bad "форма входа не отдана"; fi
else
  bad "пропущено: нет client_id"
fi

echo "== 8. Локальный клиент открывается =="
# Публичный phanpy.social до локального инстанса НЕ достучится: Chrome
# режет запросы с публичного источника на петлю (ERR_BLOCKED_BY_CLIENT),
# и заголовок Access-Control-Allow-Private-Network этого не снимает.
# Пользоваться нужно клиентом на петле.
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8083/)
if [ "$code" = "200" ]; then ok "http://127.0.0.1:8083 отвечает 200"
else bad "локальный клиент отвечает $code"; fi

echo
if [ $fail -eq 0 ]; then
  cat <<'DONE'
Все машинные проверки пройдены. Осталось действие человека:

  1. открыть http://127.0.0.1:8083/#/login?instance=feed.localtest.me
  2. нажать «Continue with feed.localtest.me»
  3. ввести почту aleks@feed.local и пароль из .env

Пароль в форму вводит человек — этот шаг намеренно не автоматизирован.
DONE
else
  echo "Есть провалы — вход не заработает, пока они не устранены."
fi
exit $fail
