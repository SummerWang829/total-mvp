#!/usr/bin/env bash
# Turn on live captions: asks for the three Tencent Cloud speech-recognition values and writes them into deploy/.env on THIS server.
# Run it on the server, from the repo root:   bash deploy/set-asr-keys.sh
# The secret key is typed hidden and is never printed or sent anywhere; the app is restarted so it takes effect.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "deploy/.env not found - run deploy/make-env.sh first"; exit 1; }

check() {  # the values are letters, digits, "_" and "-" only: refuse anything else so a stray paste cannot damage .env
  [[ "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "$1 should contain only letters, digits, _ and - (check for spaces or a wrong paste)"; exit 1; }
}
read -rp "AppID (digits, from the account info page): " appid;  check AppID "$appid"
read -rp "SecretId (starts with AKID):                " sid;    check SecretId "$sid"
read -rsp "SecretKey (hidden while you type):          " skey; echo; check SecretKey "$skey"

set_var() {  # set_var NAME VALUE: replace the line, or append it
  if grep -q "^$1=" .env; then sed -i -E "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_var TENCENT_ASR_APPID "$appid"
set_var TENCENT_ASR_SECRET_ID "$sid"
set_var TENCENT_ASR_SECRET_KEY "$skey"
chmod 600 .env

sudo docker compose up -d >/dev/null
echo "saved and restarted. Check:"
sudo docker compose exec -T app python -m app.doctor 2>&1 | grep "实时字幕"
