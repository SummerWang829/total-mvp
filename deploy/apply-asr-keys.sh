#!/usr/bin/env bash
# Writes the three Tencent speech-recognition values from a small text file into deploy/.env and restarts the app. Prints only "ok" / the reason.
# Usage (on the server, repo root):   bash deploy/apply-asr-keys.sh /path/to/file     (the file has three lines NAME=value)
# The file is only read here and is shredded afterwards; values are checked (letters, digits, _ and - only) before anything is written.
set -euo pipefail
cd "$(dirname "$0")"
src="${1:?usage: apply-asr-keys.sh FILE}"
[ -f .env ] || { echo "deploy/.env not found"; exit 1; }

get() {  # get NAME: the value after NAME= (spaces / CR / quotes stripped); empty if missing
  grep -E "^$1=" "$src" | tail -1 | cut -d= -f2- | tr -d ' \r"'"'"
}
for k in TENCENT_ASR_APPID TENCENT_ASR_SECRET_ID TENCENT_ASR_SECRET_KEY; do
  v="$(get "$k")"
  [ -n "$v" ] || { echo "$k is empty - fill all three lines"; shred -u "$src" 2>/dev/null || rm -f "$src"; exit 1; }
  [[ "$v" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "$k has characters other than letters, digits, _ and - (a stray space or wrong paste?)"; shred -u "$src" 2>/dev/null || rm -f "$src"; exit 1; }
done
set_var() { if grep -q "^$1=" .env; then sed -i -E "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
for k in TENCENT_ASR_APPID TENCENT_ASR_SECRET_ID TENCENT_ASR_SECRET_KEY; do set_var "$k" "$(get "$k")"; done
chmod 600 .env
shred -u "$src" 2>/dev/null || rm -f "$src"
sudo docker compose up -d >/dev/null 2>&1
echo "ok: saved and the app was restarted"
