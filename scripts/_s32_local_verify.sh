#!/bin/bash
cd /home/z/research
export WATCH_ONLY_TAGS=li
export WATCH_DETAILS_MAX=200
export WATCH_BUDGET_SECONDS=900
python3 scripts/gha_board_watch.py 2>&1
