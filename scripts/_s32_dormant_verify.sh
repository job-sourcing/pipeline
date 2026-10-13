#!/bin/bash
cd /home/z/research
export WATCH_ONLY_TAGS=li_dormant
export WATCH_DETAILS_MAX=60
export WATCH_BUDGET_SECONDS=600
python3 scripts/gha_board_watch.py 2>&1
