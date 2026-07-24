// Event-market harvesters — favourite-longshot off crypto (sport, politics, geo).
import { html } from '../preact.js';
import { usePoll } from '../api.js';
import { money, moneyS, pct, signCls, ageStr, dateOf } from '../format.js';
import { Kpi, Panel, Money, Empty, Tag } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';

export function Events(){
  const data = usePoll("/events-data").data;
  if(!data) return html`<div class="empty">Chargement des marchés évènements…</div>`;

  const totReal = data.reduce((a,e)=>a+e.realized_pnl, 0);
  const totOpen = data.reduce((a,e)=>a+e.open, 0);
  const totDeployed = data.reduce((a,e)=>a+e.deployed, 0);
  const now = Date.now()/1000;

  const harvCols = [
    {key:"name", label:"Stratégie", cell:e=>html`<span class="cell-name"><span class="g">🗳</span><span class="lead">${e.name.replace("events-","")}</span></span>`},
    {key:"realized_pnl", label:"P&L réglé", sortable:true, cell:e=>html`<${Money} v=${e.realized_pnl} signed=${true} />`},
    {key:"n_settled", label:"Réglées", sortable:true, cell:e=>html`<span class="num">${e.n_settled||"—"}</span>`},
    {key:"win", label:"Win / Prix", sortable:true, get:e=>e.win_rate||0,
      cell:e=>e.n_settled?html`<span class=${"num "+(e.win_rate>e.avg_price?"pos":"neg")}>${pct(e.win_rate)}</span><span class="mut"> / ${pct(e.avg_price)}</span>`:html`<span class="mut">—</span>`},
    {key:"ev", label:"Avantage/$", sortable:true, get:e=>e.ev_live||0,
      cell:e=>e.n_settled?html`<span class=${"num "+signCls(e.ev_live)}>${(e.ev_live>=0?"+":"")+e.ev_live.toFixed(3)}</span>`:html`<span class="mut">—</span>`},
    {key:"open", label:"Ouvertes", sortable:true, cell:e=>html`<span class="num">${e.open}</span>`},
    {key:"deployed", label:"$ déployé", sortable:true, cell:e=>html`<span class="num">${money(e.deployed)}</span>`},
  ];

  const allpos = data.flatMap(e => (e.positions||[]).map(p => ({...p, strat:e.name.replace("events-","")})))
    .sort((a,b)=>(a.opened||0)-(b.opened||0)).slice(0,60);
  const posCols = [
    {key:"strat", label:"Strat"},
    {key:"slug", label:"Marché", cell:p=>html`<span class="num">${String(p.slug).replace(/-\d+$/,"").slice(0,44)}</span>`},
    {key:"fav", label:"Favori", cell:p=>String(p.fav).slice(0,16)},
    {key:"price", label:"Prix", cell:p=>html`<span class="num">${(+p.price).toFixed(3)}</span>`},
    {key:"shares", label:"Parts", cell:p=>html`<span class="num">${(+p.shares).toFixed(1)}</span>`},
    {key:"opened", label:"Âge", sortable:true, get:p=>p.opened||0, cell:p=>html`<span class="num">${p.opened?ageStr(now-p.opened):"—"}</span>`},
  ];

  const allset = data.flatMap(e => (e.settled||[]).map(s => ({...s, strat:e.name.replace("events-","")})))
    .sort((a,b)=>(b.ts||0)-(a.ts||0)).slice(0,30);
  const setCols = [
    {key:"ts", label:"Heure", cell:s=>html`<span class="num">${dateOf(s.ts)}</span>`},
    {key:"strat", label:"Strat"},
    {key:"slug", label:"Marché", cell:s=>html`<span class="num">${String(s.slug).replace(/-\d+$/,"").slice(0,38)}</span>`},
    {key:"direction", label:"Favori", cell:s=>String(s.direction).slice(0,16)},
    {key:"kind", label:"Issue", cell:s=>String(s.kind).endsWith("WIN")?html`<span class="pos">gagné</span>`:html`<span class="neg">perdu</span>`},
    {key:"pnl", label:"P&L", cell:s=>html`<${Money} v=${+s.pnl} signed=${true} />`},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">🗳</span>Marchés évènements</h1>
        <p class="sub">Moisson favori-longshot hors crypto — l'edge y est plus gras (humains émotionnels,
          correction lente). <b>broad</b> = tous · <b>fast</b> = ≤5 j · <b>slow</b> = ≥14 j.</p></div>
    </div>

    <div class="kpis">
      <${Kpi} accent="gold" k="P&L réglé cumulé" v=${html`<span class=${signCls(totReal)}>${moneyS(totReal)}</span>`}
        sub=${`${data.length} moissonneurs`} />
      <${Kpi} accent="merc" k="Positions ouvertes" v=${totOpen} sub="en attente de résolution" />
      <${Kpi} k="Capital déployé" v=${money(totDeployed)} sub="immobilisé dans les paris ouverts" />
    </div>

    <${Panel} title="Par moissonneur" flush=${true}>
      <${DataTable} cols=${harvCols} rows=${data} sort=${{key:"realized_pnl",dir:-1}} empty="Aucun moissonneur." />
    </${Panel}>

    <${Panel} title="Positions ouvertes" flush=${true}>
      <${DataTable} cols=${posCols} rows=${allpos} empty="Aucune position ouverte — en attente du prochain scan." />
    </${Panel}>

    <${Panel} title="Règlements récents" flush=${true}>
      <${DataTable} cols=${setCols} rows=${allset} empty="Aucun règlement encore — résolution en heures/jours." />
    </${Panel}>`;
}
