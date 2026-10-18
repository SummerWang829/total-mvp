#!/usr/bin/env bash
# Health monitor, run by cron every 5 minutes (see deploy/README.md): checks the app container and tells the webhook (ALERT_WEBHOOK_URL in deploy/.env) when something changes.
#   */5 * * * * /opt/total-mvp/deploy/monitor.sh >> /var/log/xingtong-monitor.log 2>&1
# When the app container itself is down the app cannot report: this script then sends the message on its own through the same webhook.
set -uo pipefail
cd "$(dirname "$0")"
URL=$(grep -E '^ALERT_WEBHOOK_URL=' .env 2>/dev/null | head -1 | cut -d= -f2-)
STATE=/tmp/xingtong-monitor-host.state
if docker compose exec -T app python -m app.monitor; then exit 0; fi
rc=$?
if ! docker compose ps --status running --services 2>/dev/null | grep -qx app; then
  prev=$(cat "$STATE" 2>/dev/null || echo ok)
  if [ "$prev" = ok ] && [ -n "$URL" ]; then
    curl -fsS -m 10 -H 'Content-Type: application/json' -d '{"text":"星桐监控\n🔴 应用容器没有在运行（deploy-app-1）"}' "$URL" >/dev/null || true
  fi
  echo down > "$STATE"
else
  echo ok > "$STATE"
fi
exit $rc
