#!/bin/bash
# Consolidated paper-config dashboard: normalized return + Sharpe per config.
# Reads all paper_*.csv; column 12 = pnl, split from service env.
cd ~/rebate
declare -A SPLIT=( [paper_sz040]=40 [paper_s045]=100 [paper_sz400]=400 [paper_15m]=100 [paper_lad4]=40 )
declare -A SERIES=( [paper_sz040]=5m-L1 [paper_s045]=5m [paper_sz400]=5m [paper_15m]=15m [paper_lad4]=5m-L4 )
echo "config            series  split  n   moy/fen  norm/100p   sd   Sharpe"
for f in paper_sz040 paper_lad4 paper_s045 paper_sz400 paper_15m; do
  [ -f "$f.csv" ] || continue
  awk -F, -v sp="${SPLIT[$f]}" -v se="${SERIES[$f]}" -v nm="$f" '
    NR>1{p+=$12; sq+=$12*$12; n++}
    END{ if(n>0){ m=p/n; v=sq/n-m*m; sd=(v>0?sqrt(v):0);
      printf "%-16s  %-4s  %5d  %2d  %+7.2f  %+8.3f  %5.2f  %6.2f\n",
             nm, se, sp, n, m, 100*m/sp, sd, (sd>0?m/sd:0) } }' "$f.csv"
done
