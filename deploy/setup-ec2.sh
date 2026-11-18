#!/usr/bin/env bash
# EC2 (Ubuntu 24.04, ARM t4g 계열 권장)에서 한 번 실행: sudo bash setup-ec2.sh bot.example.com
# 하는 일: 패키지 설치 → deukgeuni 사용자 → 코드 받기 → venv → systemd → Caddy(HTTPS)
set -euo pipefail
DOMAIN="${1:?사용: sudo bash setup-ec2.sh <봇 도메인, 예: bot.example.com>}"
REPO_URL="${REPO_URL:-https://github.com/kimjihyo/deukgeuni}"

apt-get update -q
apt-get install -yq python3 python3-venv git caddy tzdata

id deukgeuni >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/deukgeuni-home --shell /bin/bash deukgeuni
if [ ! -d /opt/deukgeuni/.git ]; then
  git clone "$REPO_URL" /opt/deukgeuni
fi
chown -R deukgeuni:deukgeuni /opt/deukgeuni
sudo -u deukgeuni python3 -m venv /opt/deukgeuni/.venv
sudo -u deukgeuni /opt/deukgeuni/.venv/bin/pip install -q -r /opt/deukgeuni/bot/requirements.txt

mkdir -p /etc/deukgeuni
if [ ! -f /etc/deukgeuni/bot.env ]; then
  cp /opt/deukgeuni/deploy/.env.example /etc/deukgeuni/bot.env
  sed -i "s#^PUBLIC_URL=.*#PUBLIC_URL=https://${DOMAIN}#" /etc/deukgeuni/bot.env
  sed -i "s#^TELEGRAM_WEBHOOK_SECRET=.*#TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)#" /etc/deukgeuni/bot.env
fi
chown root:deukgeuni /etc/deukgeuni/bot.env
chmod 640 /etc/deukgeuni/bot.env

cp /opt/deukgeuni/deploy/deukgeuni-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable deukgeuni-bot

sed "s/bot.example.com/${DOMAIN}/" /opt/deukgeuni/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

cat <<EOF

설치 완료. 다음 순서:
  1) sudo nano /etc/deukgeuni/bot.env   (TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_IDS, API 키, WL_MONGO_URI 채우기)
  2) sudo systemctl start deukgeuni-bot && sudo journalctl -u deukgeuni-bot -f
  3) curl https://${DOMAIN}/healthz        → ok
  4) sudo -u deukgeuni bash -c 'cd /opt/deukgeuni && set -a && . /etc/deukgeuni/bot.env && set +a && .venv/bin/python -m bot set-webhook'
EOF
