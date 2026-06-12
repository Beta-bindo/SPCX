#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate && pip install -q -r requirements.txt
export TA_LICENSE_SKIP=1
python main.py
