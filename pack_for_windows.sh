#!/bin/bash
# 在 Mac 上运行：生成可拷到 Windows 打包的项目 zip
set -e
cd "$(dirname "$0")"
OUT="../TradeAssistant-WindowsBuild.zip"
rm -f "$OUT"
zip -r "$OUT" . \
  -x ".venv/*" \
  -x "dist/*" \
  -x "build/*" \
  -x "**/__pycache__/*" \
  -x "*.pyc" \
  -x ".git/*" \
  -x "data/*" \
  -x "installer_output/*"
echo ""
echo "已生成: $(cd .. && pwd)/TradeAssistant-WindowsBuild.zip"
echo "请将此 zip 拷到 Win10，解压后双击运行 build.bat"
