#!/usr/bin/env bash
# Builds deploy/.env on the SERVER (run there, from the repo root):  bash deploy/make-env.sh [keys-file]
#  - starts from deploy/.env.example
#  - generates SECRET_KEY and LIVEKIT_API_SECRET on the server (they never travel through chat)
#  - if a keys file is given (a copy of a developer's .env), takes ONLY its MODERATOR_* and LLM_* lines (model address, name, key, vendor)
# It never prints a secret value: only variable names and "set" / "empty".
set -euo pipefail
cd "$(dirname "$0")"
KEYS="${1:-}"
[ -f .env ] && { echo "deploy/.env already exists - not overwriting (delete it first to rebuild)"; exit 1; }

cp .env.example .env
chmod 600 .env

if [ -n "$KEYS" ]; then
  # drop the placeholder model lines, then append the developer's own
  sed -i -E '/^(MODERATOR|LLM)_[A-Z_]*=/d' .env
  grep -E '^(MODERATOR|LLM)_[A-Z_]*=' "$KEYS" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' >> .env || true
fi

set_var() {  # set_var NAME VALUE  (replace the line, or append)
  if grep -q "^$1=" .env; then sed -i -E "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
set_var SECRET_KEY "$(openssl rand -hex 32)"
set_var LIVEKIT_API_SECRET "$(openssl rand -hex 24)"

echo "deploy/.env written. Variables (values hidden):"
grep -E '^[A-Z_]+=' .env | while IFS='=' read -r k v; do
  case "$k" in
    *KEY*|*SECRET*) [ -n "$v" ] && echo "  $k = set" || echo "  $k = EMPTY" ;;
    *) echo "  $k = $v" ;;
  esac
done
