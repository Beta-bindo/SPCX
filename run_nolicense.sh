#!/usr/bin/env bash
cd "$(dirname "$0")"
if ! source .venv/bin/activate 2>/dev/null; then
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -q -r requirements.txt
fi
cat > app/core/build_config.py <<'EOF'
# Local nolicense dev - same as build_nolicense output
LICENSE_REQUIRED = False
LIVE_BOTH_ONLY = False
EOF
python3 main.py
