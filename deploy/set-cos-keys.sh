#!/usr/bin/env bash
# Turn on the off-site copy of the database backups: asks for the COS bucket and the sub-account key and writes them into deploy/.env on THIS server.
# Run it on the server, from the repo root:   bash deploy/set-cos-keys.sh
# The SecretKey is typed hidden and is never printed or sent anywhere. At the end it uploads a tiny test file to prove the key works.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "deploy/.env not found - run deploy/make-env.sh first"; exit 1; }

check() {  # letters, digits, "_" and "-" only: refuse anything else so a stray paste cannot damage .env
  [[ "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "$1 should contain only letters, digits, _ and - (check for spaces or a wrong paste)"; exit 1; }
}
read -rp "Bucket name (as shown in the console, ends with the account number): " bucket; check Bucket "$bucket"
read -rp "Region [ap-singapore]: " region; region=${region:-ap-singapore}; check Region "$region"
read -rp "SecretId of the backup sub-account (starts with AKID):            " sid;    check SecretId "$sid"
read -rsp "SecretKey (hidden while you type):                                " skey; echo; check SecretKey "$skey"

set_var() {  # set_var NAME VALUE: replace the line, or append it
  if grep -q "^$1=" .env; then sed -i -E "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_var COS_BUCKET "$bucket"
set_var COS_REGION "$region"
set_var COS_SECRET_ID "$sid"
set_var COS_SECRET_KEY "$skey"
chmod 600 .env

sudo docker compose up -d >/dev/null
sleep 6
echo "saved and restarted. Testing the upload:"
sudo docker compose exec -T app python -m app.backup --offsite-test
