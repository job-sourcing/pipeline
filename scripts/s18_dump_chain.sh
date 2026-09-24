#!/usr/bin/env bash
# s18_dump_chain.sh — full per-company dump chain for the S18 census boards
# (go-broader CN-AI round: hoyoverse / plusai / unitedimaging).
# Usage: bash s18_dump_chain.sh <label> [phase-from]
# The FULL flag set stays on EVERY phase (the S15 operational lesson:
# --company/--li-variants default to the NVIDIA pilot values and late
# phases silently mis-probe otherwise).
set -uo pipefail
LABEL="${1:?label required (hoyoverse|plusai|unitedimaging)}"
FROM="${2:-list}"
cd /home/z/my-project/job-sourcing-research

case "$LABEL" in
  hoyoverse)
    BOARD='ats:ashby:hoyoverse'; COMPANY='HoYoverse'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='HoYoverse'
    SLICES='United States;Los Angeles, California, United States;Remote, United States' ;;
  plusai)
    BOARD='ats:lever:plus-2'; COMPANY='PlusAI'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='PlusAI'
    SLICES='United States;Santa Clara, California, United States;Fremont, California, United States;Dallas, Texas, United States' ;;
  unitedimaging)
    BOARD='ats:paylocity:d527ad39-680d-45fa-9178-38a81898aec2'
    COMPANY='United Imaging'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='United Imaging,United Imaging Healthcare'
    SLICES='United States;Remote, United States;Houston, Texas, United States;Seattle, Washington, United States' ;;
  *) echo "unknown label $LABEL"; exit 2 ;;
esac

# ALWAYS pass --time-type explicitly: board_dump defaults to 'Full time'
# when the flag is absent — boards with no employment-type field
# (unitedimaging, like xiaohongshu) need the EXPLICIT empty string or
# every row drops.
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

# ashby + lever classify country at LIST time (server-authoritative
# structured fields); paylocity classifies client-side from structured
# JobLocation.Country — no countryfilter phase (netflix-class only).
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
