#!/usr/bin/env bash
# s17_dump_chain.sh — full per-company dump chain for the S17 census boards
# (CN AI-startup round: minimax / shengshu / horizon).
# Usage: bash s17_dump_chain.sh <label> [phase-from]
# The FULL flag set stays on EVERY phase (the S15 operational lesson:
# --company/--li-variants default to the NVIDIA pilot values and late
# phases silently mis-probe otherwise).
set -uo pipefail
LABEL="${1:?label required (minimax|shengshu|horizon)}"
FROM="${2:-list}"
cd /home/z/my-project/job-sourcing-research

case "$LABEL" in
  minimax)
    BOARD='ats:feishuhire:vrfi1sk8a0'; COMPANY='MiniMax'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='MiniMax,MiniMax AI'
    SLICES='United States;San Francisco, California, United States' ;;
  shengshu)
    BOARD='ats:feishuhire:shengshu'; COMPANY='Shengshu'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='Shengshu,Shengshu Technology,Vidu'
    SLICES='United States;San Francisco, California, United States' ;;
  horizon)
    BOARD='ats:lever:horizon'; COMPANY='Horizon Robotics'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='Horizon Robotics'
    SLICES='United States;Cupertino, California, United States' ;;
  xiaohongshu)
    BOARD='custom:xiaohongshu'; COMPANY='Xiaohongshu'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='rednote,Xiaohongshu'
    SLICES='United States;San Francisco, California, United States;New York, New York, United States' ;;
  *) echo "unknown label $LABEL"; exit 2 ;;
esac

# ALWAYS pass --time-type explicitly: board_dump defaults to 'Full time'
# when the flag is absent — boards with no employment-type field (xhs)
# need the EXPLICIT empty string or every row drops.
TTFLAG=(--time-type "$TTYPE")

run() { echo "== [$LABEL] $* =="; python3 scripts/board_dump.py \
  --board "$BOARD" --company "$COMPANY" --label "${LABEL}_us_fulltime" \
  --country "$COUNTRY" "${TTFLAG[@]}" --li-variants "$VARIANTS" \
  --slice-locations "$SLICES" "$@" || echo "(rc=$? — see above)"; }

phase() {
  case "$1" in
    list) run --phase list ;;
    details) run --phase details --details-batch 200 ;;
    tagfacets) run --phase tagfacets ;;
    questionnaires) run --phase questionnaires ;;
    index) run --phase corroborate --corroborate-index \
             --index-mode partitioned --li-reindex ;;
    titlesearch) run --phase titlesearch ;;
    corroborate) run --phase corroborate ;;
    finish) run --phase finish ;;
    invariants) echo "== [$LABEL] invariants =="; python3 \
      scripts/s10_invariants.py "${LABEL}_us_fulltime" || true ;;
    *) echo "unknown phase $1"; exit 2 ;;
  esac
}

# feishuhire + lever boards classify country at LIST time (server-authoritative
# structured fields) — no countryfilter phase (netflix-class only).
ORDER="list details tagfacets questionnaires index titlesearch corroborate finish invariants"
SKIP=1
for p in $ORDER; do
  if [ "$FROM" = "list" ] || [ "$SKIP" = "0" ]; then
    phase "$p"
  elif [ "$p" = "$FROM" ]; then
    SKIP=0
    phase "$p"
  fi
done
echo "== [$LABEL] CHAIN DONE =="
