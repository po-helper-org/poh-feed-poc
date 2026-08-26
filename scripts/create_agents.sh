#!/usr/bin/env bash
# Заводит аккаунты агентов и человека. Пароли одноразовые:
# токены получаются следующим скриптом, пароли дальше не нужны.
set -euo pipefail
CT=poh-feed
CFG=/gotosocial/config.yaml
PASS='PoC-feed-2026!'

for a in issue_agent openhands pr_agent howtodemo delivery harness aleks; do
  docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account create \
    --username "$a" --email "$a@feed.local" --password "$PASS" || true
  docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account confirm --username "$a" || true
done

docker exec "$CT" /gotosocial/gotosocial --config-path "$CFG" admin account promote --username aleks || true
echo "Готово. Пароль у всех: $PASS"
