#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate && pip install -q -r requirements.txt
cat > app/core/build_config.py <<'EOF'
# Local nolicense dev - same as build_nolicense output
LICENSE_REQUIRED = False
EOF
python main.py
