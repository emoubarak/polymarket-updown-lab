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
  coinman2: { tag: "coinman2", note: "prévisionniste · +$1.15M all-time", ctl: false },
  "06dc":   { tag: "0x06dc",   note: "prévisionniste · +$516k all-time",  ctl: false },
  king:     { tag: "king",     note: "contrôle négatif · −4%/20j",        ctl: true  },
};
const whoOf = e => WHO[e.target] || { tag: e.target || e.name, note: "", ctl: false };
const mkt = s => String(s||"").replace(/^(bitcoin|ethereum|solana)-above-/, "$1>")
                               .replace(/-on-.*$/, "").replace(/-\d+$/, "");

export function CopyMirror(){
  const data = usePoll("/copy-data").data;
  if(!data) return html`<div class="empty">Chargement des copies…</div>`;

  const totReal = data.reduce((a,e)=>a+e.realized_pnl, 0);
  const totMirror = data.reduce((a,e)=>a+(e.n_mirrored||0), 0);
  const totDeployed = data.reduce((a,e)=>a+e.deployed, 0);
  const now = Date.now()/1000;

  const walCols = [
    {key:"name", label:"On copie", cell:e=>{const w=whoOf(e);
      return html`<span class="cell-name"><span class="g">🪞</span>
        <span class="lead">${w.tag}</span>${w.ctl?html`<span class="mut"> (témoin)</span>`:null}</span>`;}},
    {key:"realized_pnl", label:"P&L réglé", sortable:true, cell:e=>html`<${Money} v=${e.realized_pnl} signed=${true} />`},
    {key:"n_settled", label:"Réglées", sortable:true, cell:e=>html`<span class="num">${e.n_settled||"—"}</span>`},
    {key:"win", label:"Win", sortable:true, get:e=>e.win_rate||0,
      cell:e=>e.n_settled?html`<span class="num">${pct(e.win_rate)}</span>`:html`<span class="mut">—</span>`},
    {key:"roi", label:"ROI réglé", sortable:true, get:e=>e.roi||0,
      cell:e=>e.n_settled?html`<span class=${"num "+signCls(e.roi)}>${(e.roi>=0?"+":"")+(100*e.roi).toFixed(1)}%</span>`:html`<span class="mut">—</span>`},
    {key:"n_mirrored", label:"Fills copiés", sortable:true, cell:e=>html`<span class="num">${e.n_mirrored||0}</span>`},
    {key:"open", label:"Ouvertes", sortable:true, cell:e=>html`<span class="num">${e.open}</span>`},
    {key:"deployed", label:"$ déployé", sortable:true, cell:e=>html`<span class="num">${money(e.deployed)}</span>`},
    {key:"started", label:"Depuis", sortable:true, get:e=>e.started||0,
      cell:e=>html`<span class="num">${e.started?ageStr(now-e.started):"—"}</span>`},
  ];

  const allpos = data.flatMap(e => (e.positions||[]).map(p => ({...p, who:whoOf(e).tag})))
    .sort((a,b)=>(b.opened||0)-(a.opened||0)).slice(0,80);
  const posCols = [
    {key:"who", label:"Source"},
    {key:"slug", label:"Marché", cell:p=>html`<span class="num" title=${p.title||p.slug}>${mkt(p.slug).slice(0,30)}</span>`},
    {key:"outcome", label:"Côté", cell:p=>String(p.outcome).slice(0,4)},
    {key:"price", label:"Prix moy", cell:p=>html`<span class="num">${(+p.price).toFixed(3)}</span>`},
    {key:"cost", label:"$", sortable:true, get:p=>p.cost||0, cell:p=>html`<span class="num">${money(p.cost)}</span>`},
    {key:"n_fills", label:"Fills", cell:p=>html`<span class="num">${p.n_fills||1}</span>`},
    {key:"opened", label:"Âge", sortable:true, get:p=>p.opened||0, cell:p=>html`<span class="num">${p.opened?ageStr(now-p.opened):"—"}</span>`},
  ];

  const allset = data.flatMap(e => (e.settled||[]).map(s => ({...s, who:whoOf(e).tag})))
    .sort((a,b)=>(b.ts||0)-(a.ts||0)).slice(0,30);
  const setCols = [
    {key:"ts", label:"Heure", cell:s=>html`<span class="num">${dateOf(s.ts)}</span>`},
    {key:"who", label:"Source"},
    {key:"slug", label:"Marché", cell:s=>html`<span class="num">${mkt(s.slug).slice(0,28)}</span>`},
    {key:"direction", label:"Côté", cell:s=>String(s.direction).slice(0,4)},
    {key:"kind", label:"Issue", cell:s=>String(s.kind).endsWith("WIN")?html`<span class="pos">gagné</span>`:html`<span class="neg">perdu</span>`},
    {key:"pnl", label:"P&L", cell:s=>html`<${Money} v=${+s.pnl} signed=${true} />`},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">🪞</span>Copie de wallets</h1>
        <p class="sub">Forward-test honnête du <b>copy-trading</b> : on réplique en paper chaque
          nouveau pari sur les marchés à seuil («&nbsp;BTC au-dessus de $X&nbsp;?») de wallets prévisionnistes
          repérés au hunt. Leur edge est une <b>vue BTC supérieure</b> qu'on ne peut pas reconstruire — mais
          les marchés sont lents (entrée ~48&nbsp;h avant résolution), donc on peut <b>les suivre</b> et sous-traiter
          le modèle. <b>king</b> = témoin négatif (même stratégie, anti-calibré) : coinman2 ne «&nbsp;compte&nbsp;»
          que s'il bat le témoin. On ne copie que les fills <i>postérieurs</i> au démarrage (jamais le backlog).</p></div>
    </div>

    <div class="kpis">
      <${Kpi} accent="gold" k="P&L réglé cumulé" v=${html`<span class=${signCls(totReal)}>${moneyS(totReal)}</span>`}
        sub=${`${data.length} wallets suivis`} />
      <${Kpi} accent="merc" k="Fills copiés" v=${totMirror} sub="positions répliquées" />
      <${Kpi} k="Capital déployé" v=${money(totDeployed)} sub="immobilisé dans les paris ouverts" />
    </div>

    <${Panel} title="Par wallet copié" flush=${true}>
      <${DataTable} cols=${walCols} rows=${data} sort=${{key:"realized_pnl",dir:-1}} empty="Aucun wallet suivi." />
    </${Panel}>

    <${Panel} title="Positions ouvertes (répliquées)" flush=${true}>
      <${DataTable} cols=${posCols} rows=${allpos} empty="Aucune position ouverte — en attente d'un nouveau fill de la cible." />
    </${Panel}>

    <${Panel} title="Règlements récents" flush=${true}>
      <${DataTable} cols=${setCols} rows=${allset} empty="Aucun règlement encore — les marchés à seuil résolvent en heures/jours." />
    </${Panel}>`;
}
