#!/usr/bin/env bash
# One command to run everything on your own computer: video server + the app + demo data.
#   scripts/dev.sh        then open http://localhost:8000
set -e
cd "$(dirname "$0")/.."
PORT=${PORT:-8000}
[ -f .env ] || cp .env.example .env
[ -n "$SKIP_PIP" ] || python3 -m pip install -q --disable-pip-version-check -r requirements.txt 2>/dev/null || true
[ -x bin/livekit-server ] || bash scripts/get_livekit.sh
python3 -m app.doctor || { echo "上面有必须先解决的问题"; exit 1; }
sed "s#http://127.0.0.1:8000/#http://127.0.0.1:$PORT/#" livekit.dev.yaml > /tmp/total-livekit.yaml
bin/livekit-server --config /tmp/total-livekit.yaml > /tmp/total-livekit.log 2>&1 &
LK=$!
export LIVEKIT_URL=${LIVEKIT_URL:-ws://localhost:7880}
python3 -m uvicorn app.main:app --port "$PORT" --log-level warning &
APP=$!
trap 'kill $LK $APP 2>/dev/null' EXIT INT TERM
for i in $(seq 1 30); do curl -sf "http://localhost:$PORT/api/health" >/dev/null && break; sleep 0.5; done
python3 -m app.seed --url "http://localhost:$PORT"
echo; echo "已启动。按 Ctrl+C 停止。"
wait $APP
