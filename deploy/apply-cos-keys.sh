#!/usr/bin/env bash
# Writes the COS off-site backup values from a small text file into deploy/.env, restarts the app, and uploads a tiny test file.
# Usage (on the server, repo root):   bash deploy/apply-cos-keys.sh /path/to/file     (four lines NAME=value; see secrets/cos-keys.txt)
# The file is only read here and is shredded afterwards; values are checked (letters, digits, _ and - only) before anything is written.
set -euo pipefail
cd "$(dirname "$0")"
src="${1:?usage: apply-cos-keys.sh FILE}"
[ -f .env ] || { echo "deploy/.env not found"; exit 1; }

get() {  # get NAME: the value after NAME= (spaces / CR / quotes stripped); empty if missing
  grep -E "^$1=" "$src" | tail -1 | cut -d= -f2- | tr -d ' "'"'"
}
wipe() { shred -u "$src" 2>/dev/null || rm -f "$src"; }
for k in COS_BUCKET COS_REGION COS_SECRET_ID COS_SECRET_KEY; do
  v="$(get "$k")"
  [ -n "$v" ] || { echo "$k is empty - fill all four lines"; wipe; exit 1; }
  [[ "$v" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "$k has characters other than letters, digits, _ and - (a stray space or wrong paste?)"; wipe; exit 1; }
done
set_var() { if grep -q "^$1=" .env; then sed -i -E "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
for k in COS_BUCKET COS_REGION COS_SECRET_ID COS_SECRET_KEY; do set_var "$k" "$(get "$k")"; done
chmod 600 .env
wipe
sudo docker compose up -d >/dev/null 2>&1
sleep 6
echo "saved and the app was restarted; testing the upload:"
sudo docker compose exec -T app python -m app.backup --offsite-test
