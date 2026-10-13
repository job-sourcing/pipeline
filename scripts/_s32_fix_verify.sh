#!/bin/bash
cd /home/z/research
export WATCH_ONLY_TAGS=li
python3 scripts/gha_board_watch.py --label aputure_us_fulltime 2>&1
python3 scripts/gha_board_watch.py --label grubmarket_us_fulltime 2>&1
