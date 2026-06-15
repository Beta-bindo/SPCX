#!/usr/bin/env bash
# 阿里云 Ubuntu 22.04 一键部署授权服务器
set -euo pipefail

APP_DIR="/opt/tradeassistant/server"
DATA_DIR="/var/lib/tradeassistant"
SERVICE_NAME="tradeassistant-license"
ADMIN_PASSWORD="${TA_DEPLOY_ADMIN_PASSWORD:-TradeAdmin@2026!BS}"
JWT_SECRET="${TA_DEPLOY_JWT_SECRET:-$(openssl rand -hex 32)}"

echo "==> 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl rsync

echo "==> 创建目录"
mkdir -p "$APP_DIR" "$DATA_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rsync -a --delete \
  --exclude '.env' \
  --exclude 'data/*.db' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  "$SCRIPT_DIR/" "$APP_DIR/"

echo "==> 创建 Python 虚拟环境"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> 生成管理员密码哈希"
ADMIN_HASH="$("$APP_DIR/.venv/bin/python" -c "
import sys
sys.path.insert(0, '${APP_DIR}')
from app.auth import hash_admin_password
print(hash_admin_password('${ADMIN_PASSWORD}'))
")"

echo "==> 写入 .env"
cat > "$APP_DIR/.env" <<EOF
TA_ADMIN_PASSWORD_HASH=${ADMIN_HASH}
TA_JWT_SECRET=${JWT_SECRET}
TA_DB_PATH=${DATA_DIR}/license.db
TA_HOST=0.0.0.0
TA_PORT=8787
TA_NOLICENSE_AUTO_APPROVE=0
TA_TRUST_FORWARDED=0
TA_EXPORT_MAX_ROWS=100000
EOF
chmod 600 "$APP_DIR/.env"

echo "==> 创建 systemd 服务"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=TradeAssistant License Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8787
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "==> 等待服务启动"
sleep 2
if curl -fsS "http://127.0.0.1:8787/health" | grep -q '"ok"'; then
  echo "DEPLOY OK"
else
  echo "DEPLOY FAILED: health check"
  journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
  exit 1
fi

IP="$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
echo ""
echo "=========================================="
echo "  部署成功"
echo "  健康检查: http://${IP}:8787/health"
echo "  管理后台: http://${IP}:8787/admin"
echo "  管理员密码: ${ADMIN_PASSWORD}"
echo "  JWT_SECRET: ${JWT_SECRET}"
echo "  数据库: ${DATA_DIR}/license.db"
echo "=========================================="
echo "  请妥善保存上述密码与密钥！"
echo "  客户端授权服务器地址填: http://${IP}:8787"
echo "=========================================="
