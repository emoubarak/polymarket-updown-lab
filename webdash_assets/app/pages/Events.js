// Event-market harvesters — favourite-longshot off crypto (sport, politics, geo).
import { html } from '../preact.js';
import { usePoll } from '../api.js';
import { money, moneyS, pct, signCls, ageStr, dateOf } from '../format.js';
import { Kpi, Panel, Money, Empty, Tag } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';

export function Events(){
  const data = usePoll("/events-data").data;
  if(!data) return html`<div class="empty">Loading event markets…</div>`;

  const totReal = data.reduce((a,e)=>a+e.realized_pnl, 0);
  const totOpen = data.reduce((a,e)=>a+e.open, 0);
  const totDeployed = data.reduce((a,e)=>a+e.deployed, 0);
  const now = Date.now()/1000;

  const harvCols = [
    {key:"name", label:"Strategy", cell:e=>html`<span class="cell-name"><span class="g">🗳</span><span class="lead">${e.name.replace("events-","")}</span></span>`},
    {key:"realized_pnl", label:"Settled P&L", sortable:true, cell:e=>html`<${Money} v=${e.realized_pnl} signed=${true} />`},
    {key:"n_settled", label:"Settled", sortable:true, cell:e=>html`<span class="num">${e.n_settled||"—"}</span>`},
    {key:"win", label:"Win / Prix", sortable:true, get:e=>e.win_rate||0,
      cell:e=>e.n_settled?html`<span class=${"num "+(e.win_rate>e.avg_price?"pos":"neg")}>${pct(e.win_rate)}</span><span class="mut"> / ${pct(e.avg_price)}</span>`:html`<span class="mut">—</span>`},
    {key:"ev", label:"Edge/$", sortable:true, get:e=>e.ev_live||0,
      cell:e=>e.n_settled?html`<span class=${"num "+signCls(e.ev_live)}>${(e.ev_live>=0?"+":"")+e.ev_live.toFixed(3)}</span>`:html`<span class="mut">—</span>`},
    {key:"open", label:"Open", sortable:true, cell:e=>html`<span class="num">${e.open}</span>`},
    {key:"deployed", label:"$ deployed", sortable:true, cell:e=>html`<span class="num">${money(e.deployed)}</span>`},
  ];

  const allpos = data.flatMap(e => (e.positions||[]).map(p => ({...p, strat:e.name.replace("events-","")})))
    .sort((a,b)=>(a.opened||0)-(b.opened||0)).slice(0,60);
  const posCols = [
    {key:"strat", label:"Strat"},
    {key:"slug", label:"Market", cell:p=>html`<span class="num">${String(p.slug).replace(/-\d+$/,"").slice(0,44)}</span>`},
    {key:"fav", label:"Favorite", cell:p=>String(p.fav).slice(0,16)},
    {key:"price", label:"Price", cell:p=>html`<span class="num">${(+p.price).toFixed(3)}</span>`},
    {key:"shares", label:"Shares", cell:p=>html`<span class="num">${(+p.shares).toFixed(1)}</span>`},
    {key:"opened", label:"Age", sortable:true, get:p=>p.opened||0, cell:p=>html`<span class="num">${p.opened?ageStr(now-p.opened):"—"}</span>`},
  ];

  const allset = data.flatMap(e => (e.settled||[]).map(s => ({...s, strat:e.name.replace("events-","")})))
    .sort((a,b)=>(b.ts||0)-(a.ts||0)).slice(0,30);
  const setCols = [
    {key:"ts", label:"Time", cell:s=>html`<span class="num">${dateOf(s.ts)}</span>`},
    {key:"strat", label:"Strat"},
    {key:"slug", label:"Market", cell:s=>html`<span class="num">${String(s.slug).replace(/-\d+$/,"").slice(0,38)}</span>`},
    {key:"direction", label:"Favorite", cell:s=>String(s.direction).slice(0,16)},
    {key:"kind", label:"Outcome", cell:s=>String(s.kind).endsWith("WIN")?html`<span class="pos">Won</span>`:html`<span class="neg">Lost</span>`},
    {key:"pnl", label:"P&L", cell:s=>html`<${Money} v=${+s.pnl} signed=${true} />`},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">🗳</span>Event markets</h1>
        <p class="sub">Favorite-longshot harvesting outside crypto — the edge is fatter there (emotional
          humans, slow correction). <b>broad</b> = all · <b>fast</b> = ≤5 d · <b>slow</b> = ≥14 d.</p></div>
    </div>

    <div class="kpis">
      <${Kpi} accent="gold" k="Cumulative settled P&L" v=${html`<span class=${signCls(totReal)}>${moneyS(totReal)}</span>`}
        sub=${`${data.length} harvesters`} />
      <${Kpi} accent="merc" k="Open positions" v=${totOpen} sub="awaiting resolution" />
      <${Kpi} k="Capital deployed" v=${money(totDeployed)} sub="tied up in open bets" />
    </div>

    <${Panel} title="By harvester" flush=${true}>
      <${DataTable} cols=${harvCols} rows=${data} sort=${{key:"realized_pnl",dir:-1}} empty="No harvesters." />
    </${Panel}>

    <${Panel} title="Open positions" flush=${true}>
      <${DataTable} cols=${posCols} rows=${allpos} empty="No open positions — waiting for the next scan." />
    </${Panel}>

    <${Panel} title="Recent settlements" flush=${true}>
      <${DataTable} cols=${setCols} rows=${allset} empty="No settlements yet — resolution takes hours/days." />
    </${Panel}>`;
}
