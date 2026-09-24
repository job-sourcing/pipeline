#!/usr/bin/env bash
# s16_dump_chain.sh — full per-company dump chain for the S16 census boards.
# Usage: bash s16_dump_chain.sh <label> [phase-from]
# The FULL flag set stays on EVERY phase (the S15 operational lesson:
# --company/--li-variants default to the NVIDIA pilot values and late
# phases silently mis-probe otherwise).
set -uo pipefail
LABEL="${1:?label required (gea|xpeng|faradayfuture|didi|tcl|gotion|moonshot|weride|tplink|pony)}"
FROM="${2:-list}"
cd /home/z/my-project/job-sourcing-research

case "$LABEL" in
  gea)
    BOARD='haier|wd3|GE_Appliances'; COMPANY='GE Appliances'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='GE Appliances,GE Appliances, a Haier company'
    SLICES='United States;Louisville, Kentucky, United States' ;;
  xpeng)
    BOARD='ats:greenhouse:xpengmotors'; COMPANY='XPENG'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='XPENG,XPENG Motors'
    SLICES='United States;Santa Clara, California, United States' ;;
  faradayfuture)
    BOARD='ats:greenhouse:faradayfuture'; COMPANY='Faraday Future'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='Faraday Future,Faraday Future Inc.'
    SLICES='United States;El Segundo, California, United States;Los Angeles, California, United States' ;;
  didi)
    BOARD='ats:greenhouse:didi'; COMPANY='DiDi'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='DiDi,DiDi Labs,DiDi Global'
    SLICES='United States;San Jose, California, United States' ;;
  tcl)
    BOARD='ats:greenhouse:tcl'; COMPANY='TCL'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='TCL North America,TCL'
    SLICES='United States;Irvine, California, United States' ;;
  gotion)
    BOARD='ats:greenhouse:gotion'; COMPANY='Gotion'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='Gotion Inc.,Gotion'
    SLICES='United States;Fremont, California, United States' ;;
  moonshot)
    BOARD='ats:ashby:moonshot'; COMPANY='Moonshot AI'
    COUNTRY='United States'; TTYPE=''
    VARIANTS='Moonshot AI,Moonshot AI Inc'
    SLICES='United States;New York, New York, United States' ;;
  weride)
    BOARD='ats:lever:weride'; COMPANY='WeRide'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='WeRide,WeRide Inc.'
    SLICES='United States;San Jose, California, United States' ;;
  tplink)
    BOARD='ats:workable:tp-link-usa-corp'; COMPANY='TP-Link'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='TP-Link,TP-Link Systems Inc.'
    SLICES='United States;Irvine, California, United States' ;;
  pony)
    BOARD='ats:workable:pony-dot-ai'; COMPANY='Pony.ai'
    COUNTRY='United States'; TTYPE='Full time'
    VARIANTS='Pony.ai,Pony AI'
    SLICES='United States;Fremont, California, United States' ;;
  *) echo "unknown label $LABEL"; exit 2 ;;
esac

TTFLAG=()
[ -n "$TTYPE" ] && TTFLAG=(--time-type "$TTYPE")

run() { echo "== [$LABEL] $* =="; python3 scripts/board_dump.py \
  --board "$BOARD" --company "$COMPANY" --label "${LABEL}_us_fulltime" \
  --country "$COUNTRY" "${TTFLAG[@]}" --li-variants "$VARIANTS" \
  --slice-locations "$SLICES" "$@" || echo "(rc=$? — see above)"; }

phase() {
  case "$1" in
    list) run --phase list ;;
    details) run --phase details --details-batch 200 ;;
    countryfilter) run --phase countryfilter ;;
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

# countryfilter is the netflix-class (client-country workday) phase —
# only GEA among the S16 boards; site boards classify at list time.
if [ "$LABEL" = "gea" ]; then
  ORDER="list details tagfacets questionnaires index titlesearch corroborate countryfilter finish invariants"
else
  ORDER="list details tagfacets questionnaires index titlesearch corroborate finish invariants"
fi
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
