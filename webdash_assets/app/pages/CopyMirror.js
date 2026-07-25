// Copy-mirror — paper-replicate skilled wallets' fills on crypto THRESHOLD markets.
// coinman2 / 0x06dc = forecasters (all-time +$1.15M / +$516k); king = negative control.
// Forward-test discipline: only fills AFTER we start watching are mirrored (no backlog).
import { html } from '../preact.js';
import { usePoll } from '../api.js';
import { money, moneyS, pct, signCls, ageStr, dateOf } from '../format.js';
import { Kpi, Panel, Money } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';

// short, human label for who we follow + whether it's the control
const WHO = {
  coinman2: { tag: "coinman2", note: "forecaster · +$1.15M all-time", ctl: false },
  "06dc":   { tag: "0x06dc",   note: "forecaster · +$516k all-time",  ctl: false },
  king:     { tag: "king",     note: "negative control · −4%/20d",    ctl: true  },
};
const whoOf = e => WHO[e.target] || { tag: e.target || e.name, note: "", ctl: false };
const mkt = s => String(s||"").replace(/^(bitcoin|ethereum|solana)-above-/, "$1>")
                               .replace(/-on-.*$/, "").replace(/-\d+$/, "");

export function CopyMirror(){
  const data = usePoll("/copy-data").data;
  if(!data) return html`<div class="empty">Loading copies…</div>`;

  const totReal = data.reduce((a,e)=>a+e.realized_pnl, 0);
  const totMirror = data.reduce((a,e)=>a+(e.n_mirrored||0), 0);
  const totDeployed = data.reduce((a,e)=>a+e.deployed, 0);
  const now = Date.now()/1000;

  const walCols = [
    {key:"name", label:"Copying", cell:e=>{const w=whoOf(e);
      return html`<span class="cell-name"><span class="g">🪞</span>
        <span class="lead">${w.tag}</span>${w.ctl?html`<span class="mut"> (control)</span>`:null}</span>`;}},
    {key:"realized_pnl", label:"Settled P&L", sortable:true, cell:e=>html`<${Money} v=${e.realized_pnl} signed=${true} />`},
    {key:"n_settled", label:"Settled", sortable:true, cell:e=>html`<span class="num">${e.n_settled||"—"}</span>`},
    {key:"win", label:"Win", sortable:true, get:e=>e.win_rate||0,
      cell:e=>e.n_settled?html`<span class="num">${pct(e.win_rate)}</span>`:html`<span class="mut">—</span>`},
    {key:"roi", label:"Settled ROI", sortable:true, get:e=>e.roi||0,
      cell:e=>e.n_settled?html`<span class=${"num "+signCls(e.roi)}>${(e.roi>=0?"+":"")+(100*e.roi).toFixed(1)}%</span>`:html`<span class="mut">—</span>`},
    {key:"n_mirrored", label:"Mirrored fills", sortable:true, cell:e=>html`<span class="num">${e.n_mirrored||0}</span>`},
    {key:"open", label:"Open", sortable:true, cell:e=>html`<span class="num">${e.open}</span>`},
    {key:"deployed", label:"$ deployed", sortable:true, cell:e=>html`<span class="num">${money(e.deployed)}</span>`},
    {key:"started", label:"Since", sortable:true, get:e=>e.started||0,
      cell:e=>html`<span class="num">${e.started?ageStr(now-e.started):"—"}</span>`},
  ];

  const allpos = data.flatMap(e => (e.positions||[]).map(p => ({...p, who:whoOf(e).tag})))
    .sort((a,b)=>(b.opened||0)-(a.opened||0)).slice(0,80);
  const posCols = [
    {key:"who", label:"Source"},
    {key:"slug", label:"Market", cell:p=>html`<span class="num" title=${p.title||p.slug}>${mkt(p.slug).slice(0,30)}</span>`},
    {key:"outcome", label:"Side", cell:p=>String(p.outcome).slice(0,4)},
    {key:"price", label:"Avg price", cell:p=>html`<span class="num">${(+p.price).toFixed(3)}</span>`},
    {key:"cost", label:"$", sortable:true, get:p=>p.cost||0, cell:p=>html`<span class="num">${money(p.cost)}</span>`},
    {key:"n_fills", label:"Fills", cell:p=>html`<span class="num">${p.n_fills||1}</span>`},
    {key:"opened", label:"Age", sortable:true, get:p=>p.opened||0, cell:p=>html`<span class="num">${p.opened?ageStr(now-p.opened):"—"}</span>`},
  ];

  const allset = data.flatMap(e => (e.settled||[]).map(s => ({...s, who:whoOf(e).tag})))
    .sort((a,b)=>(b.ts||0)-(a.ts||0)).slice(0,30);
  const setCols = [
    {key:"ts", label:"Time", cell:s=>html`<span class="num">${dateOf(s.ts)}</span>`},
    {key:"who", label:"Source"},
    {key:"slug", label:"Market", cell:s=>html`<span class="num">${mkt(s.slug).slice(0,28)}</span>`},
    {key:"direction", label:"Side", cell:s=>String(s.direction).slice(0,4)},
    {key:"kind", label:"Outcome", cell:s=>String(s.kind).endsWith("WIN")?html`<span class="pos">won</span>`:html`<span class="neg">lost</span>`},
    {key:"pnl", label:"P&L", cell:s=>html`<${Money} v=${+s.pnl} signed=${true} />`},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">🪞</span>Wallet copying</h1>
        <p class="sub">Honest forward-test of <b>copy-trading</b>: we paper-replicate every
          new bet on threshold markets ("BTC above $X?") from forecaster wallets
          spotted in the hunt. Their edge is a <b>superior BTC view</b> we can't reconstruct — but
          the markets are slow (entry ~48&nbsp;h before resolution), so we can <b>follow them</b> and outsource
          the model. <b>king</b> = negative control (same strategy, anti-calibrated): coinman2 only
          "counts" if it beats the control. We only copy fills <i>after</i> we start watching (never the backlog).</p></div>
    </div>

    <div class="kpis">
      <${Kpi} accent="gold" k="Cumulative settled P&L" v=${html`<span class=${signCls(totReal)}>${moneyS(totReal)}</span>`}
        sub=${`${data.length} wallets followed`} />
      <${Kpi} accent="merc" k="Mirrored fills" v=${totMirror} sub="replicated positions" />
      <${Kpi} k="Capital deployed" v=${money(totDeployed)} sub="tied up in open bets" />
    </div>

    <${Panel} title="Per copied wallet" flush=${true}>
      <${DataTable} cols=${walCols} rows=${data} sort=${{key:"realized_pnl",dir:-1}} empty="No wallet followed." />
    </${Panel}>

    <${Panel} title="Open positions (replicated)" flush=${true}>
      <${DataTable} cols=${posCols} rows=${allpos} empty="No open position — waiting for a new fill from the target." />
    </${Panel}>

    <${Panel} title="Recent settlements" flush=${true}>
      <${DataTable} cols=${setCols} rows=${allset} empty="No settlement yet — threshold markets resolve in hours/days." />
    </${Panel}>`;
}
