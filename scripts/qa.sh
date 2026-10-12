#!/usr/bin/env bash
# One command to check everything: logic tests, the whole site in a real browser, and a real 3-person video interview.
set -e
cd "$(dirname "$0")/.."
[ -x bin/livekit-server ] || bash scripts/get_livekit.sh
echo "== 1/4 logic tests =="; python3 -m pytest -q
echo "== 2/4 whole-site browser QA =="; python3 tests/qa_site.py
echo "== 3/4 real video interview, 3 browsers (moderator / participant / hidden observer) =="; python3 tests/e2e_room.py
echo "== 4/4 the one-command start (scripts/dev.sh) really works =="; python3 tests/smoke_dev.py
echo "ALL GOOD"
