#!/usr/bin/env bash
# MongoDB의 내 데이터 → 데이터 레포(workout-log)로 내보내고 커밋·푸시. cron으로 매일 실행.
# 사용: backup.sh <텔레그램ID> <데이터 레포 경로>
set -euo pipefail
TG_ID="$1"; DATA_DIR="$2"
cd /opt/deukgeuni
set -a; . /etc/deukgeuni/bot.env; set +a
git -C "$DATA_DIR" pull -q --rebase
.venv/bin/python -m bot backup --telegram-id "$TG_ID" --to "$DATA_DIR"
cd "$DATA_DIR"
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -q -m "backup: $(date +%F) from MongoDB"
  git push -q
  echo "pushed"
else
  echo "no changes"
fi
