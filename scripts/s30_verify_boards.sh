#!/bin/bash
# S30: local collection verification — the 6 new RE-wave boards through
# the FULL dump chain (list → details → finish). This is the
# "verify the run locally first" gate before dispatching GHA.
cd /home/z/research
set -x

run_board() {  # label board company
  local LBL=$1 BRD=$2 CO=$3
  python3 scripts/board_dump.py --board "$BRD" --company "$CO" \
    --label "$LBL" --country "United States" \
    --geo-scope non_cn --phase list 2>&1 | tail -4
  python3 scripts/board_dump.py --board "$BRD" --company "$CO" \
    --label "$LBL" --country "United States" \
    --geo-scope non_cn --phase details 2>&1 | tail -3
  python3 scripts/board_dump.py --board "$BRD" --company "$CO" \
    --label "$LBL" --country "United States" \
    --geo-scope non_cn --phase finish 2>&1 | tail -6
}

# a123 is a custom kind: US scope, NO geo_scope (honesty convention)
run_a123() {
  python3 scripts/board_dump.py --board "custom:a123" \
    --company "A123 Systems" --label a123_us_fulltime \
    --country "United States" --phase list 2>&1 | tail -4
  python3 scripts/board_dump.py --board "custom:a123" \
    --company "A123 Systems" --label a123_us_fulltime \
    --country "United States" --phase details 2>&1 | tail -3
  python3 scripts/board_dump.py --board "custom:a123" \
    --company "A123 Systems" --label a123_us_fulltime \
    --country "United States" --phase finish 2>&1 | tail -6
}

run_board zailab_us_fulltime "ats:smartrecruiters:ZaiLabUSLLC1" "Zai Lab"
run_board midea_us_fulltime "ats:trakstar:midea.hire.trakstar.com" "Midea America"
run_board psi_us_fulltime "ats:paycom:us-cent/39DCF574C16448FF09ADD3EF809F9EF2" "Power Solutions International"
run_board polestar_us_fulltime "ats:teamtailor:polestar" "Polestar"
run_a123
echo "=== five fast boards done; MINISO (665 rows, ~15min details) next ==="
