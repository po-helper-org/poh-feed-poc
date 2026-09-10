#!/usr/bin/env bash
# Проверяет, что все три инструмента ветки работают на живом сервере.
set -euo pipefail
BASE=http://127.0.0.1:8080
source .env
T=$FEED_TOKEN_ISSUE_AGENT
H=$FEED_TOKEN_HOWTODEMO

echo "== Инструмент 1: опрос (развилка) =="
POLL=$(curl -sS -X POST "$BASE/api/v1/statuses" -H "Authorization: Bearer $T" \
  -d 'status=Корзину хранить в сессии или в БД. Молчание 18 минут — возьму сессию.' \
  -d 'visibility=unlisted' \
  -d 'poll[options][]=Сессия' -d 'poll[options][]=БД + миграция' \
  -d 'poll[expires_in]=1080')
echo "$POLL" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["poll"],"опроса нет";print("OK, poll id",d["poll"]["id"])'

echo "== Инструмент 2: фрагмент (свёртка + большой текст) =="
BODY=$(printf '## 01 Границы задачи\n\nПромокод применяется молча.\n\n## 02 Модель корзины\n\nСрок жизни [УТОЧНИТЬ].\n')
FRAG=$(curl -sS -X POST "$BASE/api/v1/statuses" -H "Authorization: Bearer $T" \
  -d "status=$BODY" -d 'spoiler_text=БФТ · промокод из ссылки · 4210 слов' \
  -d 'content_type=text/markdown' -d 'visibility=unlisted')
echo "$FRAG" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["spoiler_text"],"свёртки нет";print("OK, свёртка:",d["spoiler_text"])'

echo "== Инструмент 3: скриншоты =="
python3 - <<'PY'
import struct, zlib, pathlib
def png(path, w=320, h=240, rgb=(20,24,26)):
    raw=b''.join(b'\x00'+bytes(rgb)*w for _ in range(h))
    def ch(t,d):
        return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d))
    pathlib.Path(path).write_bytes(
        b'\x89PNG\r\n\x1a\n'
        + ch(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
        + ch(b'IDAT', zlib.compress(raw))
        + ch(b'IEND', b''))
png('/tmp/shot1.png'); png('/tmp/shot2.png')
print('улики нарисованы')
PY
M1=$(curl -sS -X POST "$BASE/api/v2/media" -H "Authorization: Bearer $H" \
  -F file=@/tmp/shot1.png -F description='шаг 3 · ответ /healthz' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
M2=$(curl -sS -X POST "$BASE/api/v2/media" -H "Authorization: Bearer $H" \
  -F file=@/tmp/shot2.png -F description='шаг 5 · node --test' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
REP=$(curl -sS -X POST "$BASE/api/v1/statuses" -H "Authorization: Bearer $H" \
  -d 'status=Сценарий: 4 из 5. Шаг 3 упал — сервис отдаёт status, контракт ждёт ok.' \
  -d 'visibility=unlisted' -d "media_ids[]=$M1" -d "media_ids[]=$M2")
echo "$REP" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert len(d["media_attachments"])==2,"вложений не два";print("OK, вложений:",len(d["media_attachments"]))'

echo "== Ограничение: опрос и вложение вместе =="
CODE=$(curl -s -o /tmp/both.json -w '%{http_code}' -X POST "$BASE/api/v1/statuses" \
  -H "Authorization: Bearer $H" -d 'status=и то и другое' -d 'visibility=unlisted' \
  -d "media_ids[]=$M1" -d 'poll[options][]=да' -d 'poll[options][]=нет' -d 'poll[expires_in]=600')
echo "Ответ сервера: $CODE (ожидается ошибка — подтверждает, что нужны два поста)"
