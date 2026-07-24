#!/bin/bash
# Delta-neutral volume-farm dashboard. Goal: MAX rebate_eq (col15, volume/
# rebate proxy) while net (col9) stays ~0 and pnl (col12) >= 0.
# CSV: closed_at,slug,strike,close,up_wins,fills,up_sh,dn_sh,net,usdc_out,settle,pnl,fill_pnl,mk,rebate_eq_cum
cd ~/rebate
echo "config   n   fills  rebate_eq  |net|avg  pnl_tot  pnl/fen   verdict"
for f in "$@"; do
  [ -f "$HOME/rebate/paper_$f.csv" ] || { echo "$f: (no csv)"; continue; }
  awk -F, -v nm="$f" '
    NR>1 && NF>=15 {
      fills+=$6; reb=$15; an+=($9<0?-$9:$9); pnl+=$12; n++
    }
    END{ if(n>0){
      neutral=(an/n < 2.0 ? "neutre" : "DERIVE");
      prof=(pnl>=0 ? "+" : "-");
      printf "%-6s  %2d  %5d  %8.3f  %7.2f  %+7.2f  %+6.3f   %s/%s\n",
             nm, n, fills, reb, an/n, pnl, pnl/n, neutral, prof }
    }' "$HOME/rebate/paper_$f.csv"
done
