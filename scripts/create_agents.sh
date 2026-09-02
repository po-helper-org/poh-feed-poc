#!/usr/bin/env bash
# Заводит аккаунты агентов и человека. Пароли одноразовые:
# токены получаются следующим скриптом, пароли дальше не нужны.
set -euo pipefail
CT=poh-feed
CFG=/gotosocial/config.yaml

# Существующие на этом стенде аккаунты уже заведены с паролем
# пароль, заданный при их создании — при повторном запуске (пересоздании стенда) задайте
# именно его, если не меняли пароль сознательно.
if [ -z "${FEED_ACCOUNT_PASSWORD:-}" ]; then
  echo "ОШИБКА: переменная окружения FEED_ACCOUNT_PASSWORD не задана." >&2
  echo "Задайте её и запустите скрипт снова, например:" >&2
  echo "  FEED_ACCOUNT_PASSWORD='...' ./scripts/create_agents.sh" >&2
  exit 1
fi
PASS="$FEED_ACCOUNT_PASSWORD"

for a in issue_agent openhands pr_agent howtodemo delivery harness aleks; do
  docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account create \
    --username "$a" --email "$a@feed.local" --password "$PASS" || true
  docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account confirm --username "$a" || true
done

docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account promote --username aleks || true
echo "Готово. Пароль у всех: $PASS"
