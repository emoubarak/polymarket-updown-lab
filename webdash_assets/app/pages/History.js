// Archived runs — each reset snapshotted by archive.py, grouped by label.
import { html } from '../preact.js';
import { CONFIG } from '../api.js';
import { usePoll } from '../api.js';
import { money, moneyS, signCls, dateOf, nameOf, COLORS } from '../format.js';
import { Panel, Money, Empty } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';

export function History(){
  const runs = usePoll("/history", 30000).data;
  if(!runs) return html`<div class="empty">Loading history…</div>`;

  const groups = new Map();
  runs.forEach(r => { const k = r.label + "|" + Math.floor(r.archived_at/600);
    if(!groups.has(k)) groups.set(k, []); groups.get(k).push(r); });
  const list = [...groups.values()].reverse();

  const cols = [
    {key:"strategy", label:"Strategy", cell:r=>html`<span class="lead">${nameOf(r.strategy)}</span>`},
    {key:"pnl", label:"Result", sortable:true, cell:r=>html`<${Money} v=${r.pnl} signed=${true} />`},
    {key:"fees_paid", label:"Fees", cell:r=>html`<span class="num">${money(r.fees_paid)}</span>`},
    {key:"n_trades", label:"Bets", sortable:true, cell:r=>html`<span class="num">${r.n_trades}</span>`},
    {key:"win", label:"Win rate", cell:r=>html`<span class="num">${r.n_trades?Math.round(100*r.n_wins/r.n_trades)+" %":"—"}</span>`},
    {key:"final_equity", label:"Final capital", cell:r=>html`<span class="num">${money(r.final_equity)}</span>`},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">🕰</span>History</h1>
        <p class="sub">Every reset of the race is archived before being wiped — past runs, grouped by label.</p></div>
    </div>
    ${!list.length ? html`<${Empty}>No archived run yet.</${Empty}>`
      : list.map(g => {
          const t0 = Math.min(...g.map(r=>r.started||r.archived_at));
          const t1 = Math.max(...g.map(r=>r.ended||r.archived_at));
          const tot = g.reduce((a,r)=>a+r.pnl, 0);
          const series = g.filter(r=>r.equity && r.equity.length>1).map(r=>({pts:r.equity, name:nameOf(r.strategy),
            color:COLORS[CONFIG.STRATS.indexOf(r.strategy)%COLORS.length] || COLORS[0]}));
          const rows = g.slice().sort((a,b)=>b.pnl-a.pnl);
          return html`<${Panel} title=${g[0].label}
            aside=${html`${dateOf(t0)} → ${dateOf(t1)} · <span class=${signCls(tot)}>total ${moneyS(tot)}</span>`}>
            ${series.length ? html`<${LineChart} series=${series} base=${g[0].initial||1000} height=${230} />` : null}
            <div style="margin-top:14px"><${DataTable} cols=${cols} rows=${rows} /></div>
          </${Panel}>`;
        })}`;
}
