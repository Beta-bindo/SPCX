#!/usr/bin/env bash
# macOS 打包：生成 dist/TradeAssistant.app
set -euo pipefail
cd "$(dirname "$0")"

echo "========================================"
echo "  交易助手 - macOS 打包"
echo "========================================"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

rm -rf build dist
python -m PyInstaller build.spec --noconfirm --clean

if [[ -d dist/TradeAssistant.app ]]; then
  echo ""
  echo "打包成功: $(pwd)/dist/TradeAssistant.app"
  du -sh dist/TradeAssistant.app
else
  echo "未找到 dist/TradeAssistant.app，请检查 build 日志"
  exit 1
fi
