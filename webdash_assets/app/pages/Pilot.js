// The pilot — the only page where REAL money moves. Several pilots run in
// PARALLEL now: a list of every run (arm/stop/forget) + a detail view of the
// selected one. All armed pilots share the one wallet (see the shared-wallet
// warning) — DRY-RUN pilots are fully isolated.
import { html, useState } from '../preact.js';
import { CONFIG, usePoll, postPilot } from '../api.js';
import { money, moneyS, pct, signCls, timeOf, dirLabel, kindLabel, glyphOf, effectiveSlot, gateSummary, fmt0, isMakerName, takerTwin, betMax, isSettleKind } from '../format.js';
import { Kpi, Panel, Tag, Empty, Money } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';

const DEFAULTS = {strategy:"zlead", interval:"15m", underlying:"btc", start:20, stake:5,
  weighted:false, weight_pct:10, bet_max:50, enter_lo:""};

// Dropdown options derived from the central registry served by /config (coins.py), with a
// literal fallback before the config lands. Coins become [value, LABEL] pairs (BTC, ETH, …).
const frameOpts = () => (CONFIG.FRAMES && CONFIG.FRAMES.length) ? CONFIG.FRAMES : ["5m","15m"];
const coinOpts  = () => ((CONFIG.COINS && CONFIG.COINS.length) ? CONFIG.COINS
  : ["btc","eth","sol","xrp","doge","bnb"]).map(k => [k, k.toUpperCase()]);

const sizeTxt = cc => cc.weighted
  ? `pondéré ${Math.round((cc.weight_pct||0)*100)} % (max $${(+cc.bet_max).toFixed(0)})`
  : `flat $${(+cc.stake).toFixed(0)}/pari`;
const modeOf = p => !p.alive ? "arrêté" : (p.status.mode==="live" ? "🔴 RÉEL" : "DRY-RUN");
const isLive = p => p.alive && p.status.mode==="live";
const cfgLabel = c => `${c.strategy} · ${String(c.underlying||"").toUpperCase()} ${c.interval}`;

// Pull a pilot's stored config back into the edit form. weight_pct is stored as a
// fraction (0.10) but the form edits a percentage (10); enter_lo may be null -> "".
const cfgToForm = c => ({strategy:c.strategy, interval:c.interval, underlying:c.underlying,
  start: c.start ?? 20, stake: c.stake ?? 5, weighted: !!c.weighted,
  weight_pct: Math.round((c.weight_pct ?? 0.10) * 100), bet_max: c.bet_max ?? 50,
  enter_lo: (c.enter_lo === null || c.enter_lo === undefined) ? "" : c.enter_lo});

export function Pilot({all}){
  const d = usePoll("/pilot-data", 6000).data;
  const [cfg, setCfg] = useState(DEFAULTS);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null);    // id of the pilot being edited (null = "Ajouter")
  const set = (k,v) => setCfg(o => ({...o, [k]: v}));

  function startEdit(p){                            // load a pilot into the form, lock its identity
    setCfg(cfgToForm(p.status.config || {}));
    setEditing(p.id);
    if(typeof window !== "undefined") window.scrollTo({top:0, behavior:"smooth"});
  }
  function cancelEdit(){ setEditing(null); setCfg(DEFAULTS); }

  async function act(a, id){
    const pilots = (d && d.pilots) || [];
    const p = id ? pilots.find(x => x.id === id) : null;
    if(a === "start_live"){
      const sz = cfg.weighted ? `pondéré ${cfg.weight_pct} % du capital (max $${cfg.bet_max})` : `flat $${cfg.stake}/pari`;
      const nLive = pilots.filter(isLive).length;
      const extra = nLive >= 1 ? `\n\n⚠ ${nLive} pilote(s) réel(s) déjà armé(s) — ils PARTAGENT le même wallet : `
        + `chacun lit le solde entier comme son capital (sur-mise possible).` : "";
      // maker entry IS implemented in live.py now (post_only GTD + taker fallback).
      const mk = /mk$/.test(cfg.strategy) ? `\n\n• ${cfg.strategy} = entrée MAKER réelle `
        + `(ordre au repos post_only + repli taker tardif). Chemin neuf, teste-le avec une petite mise.` : "";
      if(!confirm(`⚠ ARMER UN PILOTE RÉEL\n\nDe VRAIS ordres seront envoyés sur Polymarket :\n`
        + `${cfg.strategy} · ${String(cfg.underlying).toUpperCase()} · ${cfg.interval} · ${sz}\n`
        + `Capital = solde réel du wallet (partagé).${extra}${mk}\n\nDe l'argent que tu peux perdre. Confirmer l'armement ?`)) return;
    }
    if(a === "resume" && p && p.status.armed_mode === "live"
      && !confirm(`⚠ REPRENDRE EN RÉEL\n\n${id} repart en argent réel — de VRAIS ordres seront à nouveau `
        + `envoyés sur Polymarket. Confirmer la reprise ?`)) return;
    if(a === "edit" && p && isLive(p)
      && !confirm(`⚠ ${id} est en RÉEL.\n\nAppliquer les nouveaux paramètres redémarre le pilote `
        + `(bref arrêt puis reprise en argent réel, état conservé). Confirmer ?`)) return;
    if(a === "forget" && !confirm("Supprimer ce pilote de la liste ?\nSon historique sur disque est conservé (relancer la même config le récupère).")) return;

    let body;
    if(a === "stop" || a === "resume" || a === "forget"){
      body = new URLSearchParams({action:a, id}).toString();
    } else if(a === "edit"){                        // identity stays locked server-side; send only staking params
      body = new URLSearchParams({action:"edit", id, start:cfg.start, stake:cfg.stake,
        weighted:cfg.weighted?"1":"0", weight_pct:(cfg.weight_pct/100).toString(),
        bet_max:cfg.bet_max, enter_lo:cfg.enter_lo||""}).toString();
    } else {                                        // start_dry | start_live
      body = new URLSearchParams({action:a, strategy:cfg.strategy, interval:cfg.interval, underlying:cfg.underlying,
        start:cfg.start, stake:cfg.stake, weighted:cfg.weighted?"1":"0",
        weight_pct:(cfg.weight_pct/100).toString(), bet_max:cfg.bet_max, enter_lo:cfg.enter_lo||""}).toString();
    }
    try {
      const r = await postPilot(body);
      alert(r.msg || (r.ok ? "ok" : "échec"));
      if(r.ok && a === "edit") cancelEdit();        // applied -> leave edit mode, reset the form
    } catch(e){ alert("erreur réseau : " + e); }
  }

  if(!d) return html`<div class="empty">Chargement du pilote…</div>`;
  const pilots = d.pilots || [];
  const nLive = pilots.filter(isLive).length;
  const sp = pilots.find(p => p.id === selected) || pilots[0] || null;

  // Global win-rate across the strategies CURRENTLY RUNNING (alive pilots), weighted by
  // each pilot's settled-trade count. Shown vs the avg entry price so it reads as EDGE,
  // not a bare number: a 95% win only beats the market if the price paid was below it
  // (the tripwire). A paused pilot (e.g. a cut unstable one) drops out immediately.
  const liveP = pilots.filter(p => p.alive);
  const gN = liveP.reduce((s,p)=>s+(p.n_trades||0),0);
  const gWin = gN ? liveP.reduce((s,p)=>s+(p.win_rate||0)*(p.n_trades||0),0)/gN : 0;
  const gPx  = gN ? liveP.reduce((s,p)=>s+(p.avg_price||0)*(p.n_trades||0),0)/gN : 0;
  const gEdge = gWin - gPx;

  const sel = (key, opts, disabled=false) => html`<select class="ctl" value=${cfg[key]} disabled=${disabled}
    onChange=${e=>set(key, e.target.value)}>
    ${opts.map(o => { const [v,l] = Array.isArray(o)?o:[o,o]; return html`<option value=${v}>${l}</option>`; })}</select>`;
  const num = (key, step=1) => html`<input class="ctl" type="number" step=${step} value=${cfg[key]}
    onInput=${e=>set(key, +e.target.value)} />`;

  const cols = [
    {key:"mode", label:"État", cell:p=>html`<${Tag} tone=${isLive(p)?"real":""}>${modeOf(p)}</${Tag}>`},
    {key:"strat", label:"Stratégie", get:p=>p.id, cell:p=>{ const c=p.status.config||{};
      return html`<span class="cell-name">${sp&&sp.id===p.id?html`<span class="g">▸</span>`:null}
        <span class="g">${glyphOf(c.strategy||"")}</span> ${cfgLabel(c)}</span>`; }},
    {key:"size", label:"Mise", cell:p=>html`<span class="mut">${sizeTxt(p.status.config||{})}</span>`},
    {key:"bankroll", label:"Capital", sortable:true, get:p=>p.bankroll, cell:p=>html`<span class="num">${money(p.bankroll)}</span>`},
    {key:"pnl", label:"P&L réglé", sortable:true, get:p=>p.realized_pnl, cell:p=>html`<${Money} v=${p.realized_pnl} signed=${true} />`},
    {key:"n", label:"Paris", sortable:true, get:p=>p.n_trades, cell:p=>html`<span class="num">${p.n_trades||0}</span>`},
    {key:"act", label:"", cell:p=>{ const stop = e=>e.stopPropagation();
      return p.alive
        ? html`<span class="row-actions" style="display:inline-flex;gap:6px">
            <button class="btn sm" onClick=${e=>{stop(e); act("stop", p.id);}}>⏸ Pause</button>
            <button class="btn sm" onClick=${e=>{stop(e); startEdit(p);}}>✎ Éditer</button></span>`
        : html`<span class="row-actions" style="display:inline-flex;gap:6px">
            <button class="btn sm ${p.status.armed_mode==="live"?"danger":""}"
              onClick=${e=>{stop(e); act("resume", p.id);}}>▶ Reprendre</button>
            <button class="btn sm" onClick=${e=>{stop(e); startEdit(p);}}>✎ Éditer</button>
            <button class="btn sm danger" onClick=${e=>{stop(e); act("forget", p.id);}}>✕ Supprimer</button></span>`;
    }},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">⚗</span>Le pilote — argent réel</h1>
        <p class="sub">Le seul endroit où de l'argent réel se déplace. ${pilots.length} pilote${pilots.length>1?"s":""}
          (${nLive} en réel) · plusieurs stratégies en parallèle.</p></div>
      <div class="head-aside">${d.armed_env ? html`<${Tag} tone="real">clé prête</${Tag}>` : html`<${Tag}>clé absente</${Tag}>`}</div>
    </div>

    ${(gN > 0 || (d.wallet && d.wallet.capital > 0)) ? html`<div class="kpis" style="margin-bottom:16px">
      <${Kpi} k="Win-rate global · stratégies en cours"
        v=${gN ? pct(gWin,1) : "—"}
        sub=${gN ? html`<span class="mut">${gN} trades réglés · prix ${pct(gPx,1)} · </span><span class=${signCls(gEdge)}>edge ${gEdge>=0?"+":""}${(gEdge*100).toFixed(1)} pt</span>` : "aucune stratégie en cours"} />
      ${d.wallet && d.wallet.capital > 0 ? html`
        <${Kpi} real=${true} k="P&L temps réel — wallet (mark-to-market)"
          v=${html`<span class=${signCls(d.wallet.pnl)}>${moneyS(d.wallet.pnl)}</span>`}
          sub=${`capital ${money(d.wallet.capital)} · dépôt ${money(d.wallet.baseline)}`} />
        <${Kpi} k="Capital réel (wallet partagé)" v=${money(d.wallet.capital)}
          sub=${`${d.wallet.n_live} pilote${d.wallet.n_live>1?"s":""} en réel · marqué temps réel`} />` : null}
    </div>` : null}

    <div class="howto warn">⚠ <b>Argent réel.</b> « Armer » envoie de VRAIS ordres sur Polymarket. Commence par
      <b>DRY-RUN</b> (zéro argent, zéro clé). Le réel exige la clé dans l'env du dashboard + un <code>.venv-live</code>
      sur le VPS (voir <code>docs/SETUP-LIVE.md</code>). On peut lancer <b>autant de pilotes qu'on veut en parallèle</b>
      (un par couple stratégie/actif/frame).</div>

    ${nLive >= 2 ? html`<div class="note-box bad" style="margin-bottom:16px">⚠ <b>${nLive} pilotes réels partagent un seul wallet.</b>
      Le « Capital » affiché = solde pUSD liquide <b>+ valeur de TOUTES les positions ouvertes</b> (le wallet entier) —
      le même nombre pour chaque pilote, qui ne dépend plus de qui trade en même temps. Chacun mise une fraction de ce
      total partagé, donc le total engagé peut dépasser le wallet. Le <b>P&L réglé</b> reste exact (par journal).</div>` : null}

    <${Panel} title=${editing
        ? html`Éditer — <span class="glyph" style="font-size:18px">${glyphOf(cfg.strategy||"")}</span> ${editing}`
        : "Ajouter un pilote"}
      aside=${editing ? html`<span class="mut">identité verrouillée · seuls les paramètres de mise changent</span>` : null}>
      <div class="controls" style="margin-bottom:16px">
        <span class="field"><b>Stratégie</b> ${sel("strategy", CONFIG.FAMILY, !!editing)}</span>
        <span class="field"><b>Frame</b> ${sel("interval", frameOpts(), !!editing)}</span>
        <span class="field"><b>Actif</b> ${sel("underlying", coinOpts(), !!editing)}</span>
        <span class="field"><b>Capital</b> ${num("start",5)} $</span>
        ${cfg.weighted
          ? html`<span class="field"><b>Pondération</b> ${num("weight_pct")} % · <b>max</b> ${num("bet_max",5)} $
              <span class="mut" title="capacité mono-acteur dans 0,85–0,95 pour ce coin ET cette frame (médiane du plus gros wallet seul/fenêtre, concurrence incluse — mesurée)">· plafond carnet ${String(cfg.underlying).toUpperCase()} ${cfg.interval} $${fmt0(betMax(cfg.underlying, cfg.interval, CONFIG.COIN_DEPTH, 50))}</span></span>`
          : html`<span class="field"><b>Mise/pari</b> ${num("stake")} $</span>`}
        <span class="field"><b>Pondérer</b> <input type="checkbox" checked=${cfg.weighted} onChange=${e=>set("weighted", e.target.checked)} /></span>
        <span class="field"><b>Fenêtre enter_lo</b> <input class="ctl" type="number" step="0.01" min="0.05" max="0.5" style="width:88px"
          placeholder="auto/coin" value=${cfg.enter_lo} onInput=${e=>set("enter_lo", e.target.value)} /></span>
      </div>
      ${(() => { const p = (CONFIG.PRESETS || {})[cfg.strategy]; if(!p) return null;
        const cs = CONFIG.COIN_SLOTS || {}, over = cfg.underlying in cs;
        return html`<div class="howto" style="margin-bottom:16px">⚙ <b>${cfg.strategy}</b> · ${String(cfg.underlying).toUpperCase()} :
          ${gateSummary(p)} · <b>entrée ${effectiveSlot(p, cfg.interval, cfg.underlying, cs)}</b>${over?html` <span class="mut">(réglage propre à ${String(cfg.underlying).toUpperCase()})</span>`:""}${cfg.enter_lo?html` <span class="mut">· override ${cfg.enter_lo}</span>`:""}</div>`; })()}
      ${/mk$/.test(cfg.strategy) ? html`<div class="note-box" style="margin-bottom:16px">ℹ <b>${cfg.strategy}</b>
        = entrée <b>maker</b> : pose un ordre d'achat passif au prix du favori (<code>post_only</code> = vrai maker,
        zéro frais taker, récupère le spread) ; s'il n'est pas rempli à <b>${"<15 %"}</b> de la fenêtre, il est annulé
        et on traverse en <b>taker</b> pour ne pas rater un favori parti tout droit. Implémenté en réel
        (GTD auto-expirant). <b>Chemin neuf</b> : teste-le avec une <b>petite mise</b> avant de monter — sa thèse
        (gagner le spread) n'est validée qu'en papier pour l'instant.</div>` : null}
      <div class="controls">
        ${editing
          ? html`<button class="btn primary" onClick=${()=>act("edit", editing)}>✓ Appliquer</button>
              <button class="btn" onClick=${cancelEdit}>Annuler</button>
              <span class="field mut">redémarre ${editing} avec les nouveaux paramètres (état &amp; piste conservés)</span>`
          : html`<button class="btn primary" onClick=${()=>act("start_dry")}>▶ Lancer DRY-RUN</button>
              <button class="btn danger" onClick=${()=>act("start_live")}>🔴 Armer le RÉEL</button>
              <span class="field mut">identité = ${cfg.strategy}-${cfg.underlying}-${cfg.interval} (un seul par identité)</span>`}
      </div>
    </${Panel}>

    <${Panel} title="Pilotes" flush=${true}
      aside=${pilots.length?html`<span class="mut">clique une ligne pour le détail · ⏸ pause · ✎ édite la mise · ▶ reprend · ✕ supprime</span>`:null}>
      <${DataTable} empty="Aucun pilote. Configure-en un ci-dessus et lance le DRY-RUN."
        rows=${pilots} cols=${cols} onRowClick=${p=>setSelected(p.id)} />
    </${Panel}>

    <${PilotProjections} pilots=${pilots} all=${all} />

    ${sp ? html`<${PilotDetail} sp=${sp} />` : null}`;
}

function PilotDetail({sp}){
  const c = sp.status.config || {};
  const pnl = sp.realized_pnl;
  const trip = sp.n_trades ? (sp.win_rate>sp.avg_price ? "pos" : "neg") : "";
  const started = sp.status.started;
  return html`
    <${Panel} title=${html`Détail — <span class="glyph" style="font-size:18px">${glyphOf(c.strategy||"")}</span> ${cfgLabel(c)}`}
      aside=${html`<span class="mut">${modeOf(sp)} · ${sizeTxt(c)}${sp.alive&&started?` · depuis ${timeOf(started)}`:""}</span>`}>
      <div class="kpis">
        <${Kpi} real=${true} k="Capital (wallet réel)" v=${money(sp.bankroll)} sub=${`pic ${money(sp.peak)}`} />
        <${Kpi} accent=${pnl>=0?"gold":""} k="P&L réel réglé"
          v=${html`<span class=${signCls(pnl)}>${moneyS(pnl)}</span>`} sub=${`pire creux ${money(-(sp.realized_dd||0))}`} />
        <${Kpi} k="Paris réglés" v=${sp.n_trades||0} sub=${`phase : ${sp.phase}`} />
        <${Kpi} k="Réussite vs prix" num=${false} tone=${trip}
          v=${sp.n_trades?`${pct(sp.win_rate)} / ${pct(sp.avg_price)}`:"—"}
          sub=${sp.n_trades?`${sp.n_trades}/150 vers le verdict`:"en attente du 1ᵉʳ pari"} />
      </div>
    </${Panel}>

    <${Panel} title="Évolution des résultats — P&L réglé cumulé"
      aside=${sp.n_trades?html`<span class="mut">${sp.n_trades} paris · creux max ${money(-(sp.realized_dd||0))}</span>`:null}>
      ${(sp.pnl_curve||[]).length>=2
        ? html`<${LineChart} series=${[{pts:sp.pnl_curve, color:"#e8c264", name:"P&L réglé cumulé"}]}
            base=${0} single=${true} height=${240} />`
        : html`<${Empty}>Courbe disponible dès le 2ᵉ pari réglé.</${Empty}>`}
    </${Panel}>

    <${Panel} title="Position en cours" flush=${true}>
      ${sp.position ? html`<${DataTable} rows=${[sp.position]} cols=${[
        {key:"slug", label:"Marché", cell:p=>html`<span class="num">${String(p.slug||"").replace("btc-updown-","")}</span>`},
        {key:"direction", label:"Sens", cell:p=>dirLabel(p.direction)},
        {key:"shares", label:"Parts", cell:p=>html`<span class="num">${p.shares}</span>`},
        {key:"price", label:"Prix", cell:p=>html`<span class="num">${(+p.price).toFixed(3)}</span>`},
      ]} />` : html`<${Empty}>Aucune — en attente d'un favori net avec lead établi.</${Empty}>`}
    </${Panel}>

    <${Panel} title="Journal du pilote" flush=${true}>
      <${DataTable} empty="Aucun pari encore. Lance le DRY-RUN pour voir les ordres simulés."
        rows=${(sp.trades||[]).slice(-30).reverse()} cols=${journalCols} />
    </${Panel}>`;
}

const amt = r => { const k=(r.kind||"").toUpperCase(), sh=+r.shares||0, px=+r.price||0;
  if(k.startsWith("BUY")||k.startsWith("FILL_BUY")) return -(sh*px);   // taker or maker fill = $ spent
  if(k.endsWith("WIN")) return sh; if(k.endsWith("LOSS")) return 0; return null; };  // REST_BUY/CANCEL = no $
const hasPnl = r => r.pnl!=="" && r.pnl!==undefined && r.pnl!==null;
const journalCols = [
  {key:"ts", label:"Heure", cell:r=>html`<span class="num">${timeOf(r.ts)}</span>`},
  {key:"kind", label:"Type", cell:r=>kindLabel(r.kind||"")},
  {key:"dir", label:"Sens", cell:r=>dirLabel(r.direction)},
  {key:"shares", label:"Parts", cell:r=>html`<span class="num">${r.shares?(+r.shares).toFixed(2):"—"}</span>`},
  {key:"price", label:"Prix", cell:r=>html`<span class="num">${r.price?(+r.price).toFixed(3):"—"}</span>`},
  {key:"amt", label:"Montant", cell:r=>{const a=amt(r); return a!==null?html`<span class=${"num "+(a!==0?signCls(a):"")}>${moneyS(a)}</span>`:html`<span class="mut">—</span>`;}},
  {key:"pnl", label:"P&L", cell:r=>hasPnl(r)?html`<${Money} v=${+r.pnl} signed=${true} />`:html`<span class="mut">—</span>`},
  {key:"bankroll", label:"Capital", cell:r=>html`<span class="num">${r.bankroll?money(+r.bankroll):"—"}</span>`},
];

// ---- gains projection — GLOBAL over every RUNNING pilot, with per-pilot tabs ----
// "Tous" = one RÉEL-lens row per running pilot + a fleet TOTAL ; a pilot tab = its 3
// lenses (paper twin live · backtest OOS · this pilot's REAL realized) × 5 horizons,
// each sized with the pilot's own staking. WEIGHTED pilots COMPOUND (bet grows with
// capital, capped at book depth) ; flat pilots stay linear.
const PROJ_H = [[1,"1 jour"],[30,"1 mois"],[90,"3 mois"],[180,"6 mois"],[365,"1 an"]];

// The pilot's OWN realized edge/$ + trades/day, from its real settled trades — same
// formulas as the server metrics (settled_pnl/settled_stake ; n/run_days).
function pilotEdge(sp){
  const settled = (sp.trades||[]).filter(t => isSettleKind(t.kind));   // shared journal classifier
  let pnl = 0, staked = 0;
  for(const t of settled){ const p=+t.pnl, sh=+t.shares, px=+t.price;
    if(isFinite(p) && sh>0 && px>0){ pnl += p; staked += sh*px; } }
  const ev = staked>0 ? pnl/staked : null;
  const curve = sp.pnl_curve || [];
  const days = curve.length>=2 ? (curve[curve.length-1][0]-curve[0][0])/86400 : 0;
  const tpd = days>0 ? (sp.n_trades||0)/days : null;
  return {ev, tpd, n:sp.n_trades||0};
}

// Projected GAIN ($) over `days`. WEIGHTED (% of capital) = COMPOUNDED day-by-day: the
// bet grows with capital, capped at the book-depth limit (sz.cap). FLAT = linear (a fixed
// bet can't compound). `fill` (0–1) scales the stake actually filled — < 1 models a book
// too thin to fill the intended size. ⚠ optimistic ceiling — NO slippage/regime haircut
// yet (we'll tune later), so compounding a thin high-freq edge inflates fast.
// CANONICAL single-bet sizing lives in format.effStake (= staking.weighted_clip): frac of
// capital, FLOORED at min_clip $5, capped at the coin's book depth and the capital. This
// projection clip is DELIBERATELY simpler — min(frac·B, cap) with NO $5 floor and no cent
// rounding — because it's a smooth multi-day COMPOUNDING sim: applying the $5 floor would
// distort the curve for small-capital pilots (the $20 default start), and per-bet rounding
// adds nothing here. Kept divergent on purpose; effStake remains the source for the real bet.
function projGain(ev, tpd, base, sz, days, fill=1){
  if(ev==null || tpd==null || !(tpd>0)) return null;
  if(!sz.weighted) return ev * (sz.stake*fill) * tpd * days;  // flat: linear
  let B = base;                                              // weighted: compound, depth-capped
  for(let d=0; d<days; d++){ const clip = Math.min(sz.frac*B, sz.cap) * fill; B += ev*clip*tpd;
    if(B < 0.01) return -base; }
  return B - base;
}

// Uncertainty on the projected CAPITAL (base+gain), DECOMPOSED into the two error sources
// (kept SEPARATE so each delta is visible, per the user's ask):
//   • win  = the trade success probability drifts ±WIN_UNC, at FULL fill. For a favorite
//            bought at price p, EV/$ = winrate/p − 1, so a ±dwr win-rate shift moves EV/$
//            by ±dwr/p — the dominant, edge-flipping risk on a thin compounded edge.
//   • fill = the book fills only FILL_LO…FILL_HI of the intended size (depth), at the
//            central win-rate. One bound is the central capital (100 % fill); the other is
//            the depth-starved 10 % fill (our live BTC sweep: $1200 ≈ full at the favorite,
//            thin coins under-fill — that's the spread this captures).
// Gain is monotonic in both inputs, so each band is just its two endpoints (Math.min/max
// handles the sign flip: a thinner fill SHRINKS a loss as well as a gain).
const WIN_UNC = 0.03;                       // ±3 % on the trade success probability
const FILL_LO = 0.10, FILL_HI = 1.0;        // 10–100 % of the intended fill (book depth)
function projRanges(ev, tpd, base, sz, days, px){
  if(ev==null || tpd==null || !(tpd>0)) return null;
  const p = px>0 ? px : 0.90;               // representative entry price (favorite band)
  const dev = WIN_UNC / p;                  // win-rate ±0.03 → EV/$ ±0.03/p
  const cap = (e, f) => { const g = projGain(e, tpd, base, sz, days, f); return g==null ? null : base + g; };
  const wlo = cap(ev - dev, FILL_HI), whi = cap(ev + dev, FILL_HI);    // win-rate band @ full fill
  const flo = cap(ev, FILL_LO),       fhi = cap(ev, FILL_HI);          // fill band @ central win-rate
  if(wlo == null || flo == null) return null;
  return {win:  {lo: Math.min(wlo, whi), hi: Math.max(wlo, whi)},
          fill: {lo: Math.min(flo, fhi), hi: Math.max(flo, fhi)}};
}

// Gather one pilot's projection inputs: sizing + the 3 lenses.
function pilotProj(sp, all){
  const c = sp.status.config || {};
  const twinName = `${c.strategy}-${c.underlying}-${c.interval}`;
  const tw = (all && all[twinName] && all[twinName].metrics) || {};
  let evBt = tw.ev_prior, tpdBt = tw.tpd_prior;       // maker borrows the taker twin's OOS prior
  if((evBt==null || tpdBt==null) && isMakerName(twinName)){
    const tt = all && all[takerTwin(twinName)] && all[takerTwin(twinName)].metrics;
    if(tt){ if(evBt==null) evBt = tt.ev_prior; if(tpdBt==null) tpdBt = tt.tpd_prior; }
  }
  const real = pilotEdge(sp);
  const base = sp.bankroll || c.start || 20;
  const sz = c.weighted
    ? {weighted:true, frac:(c.weight_pct||0.10), cap:(+c.bet_max||50)}
    : {weighted:false, stake:(+c.stake||5)};
  const szTxt = c.weighted
    ? `composé ${Math.round((c.weight_pct||0.10)*100)} %/pari (max $${(+c.bet_max||50).toFixed(0)})`
    : `mise fixe $${(+c.stake||5).toFixed(0)}/pari`;
  // px = representative entry price for the ±3 % win-rate→EV/$ band (projRanges). Paper &
  // backtest reuse the paper twin's avg fill (same favorite band); real uses this pilot's.
  const pxTwin = tw.avg_price || 0, pxReal = sp.avg_price || 0;
  const SRC = [
    {key:"paper", label:"Paper — papier, même strat", color:"#e8c264", ev:(tw.ev_live??null), tpd:(tw.tpd_live??null), n:tw.n_settled||0, px:pxTwin},
    {key:"bt",    label:"Backtest — OOS (passé)",      color:"#a99c80", ev:(evBt??null),       tpd:(tpdBt??null),      n:null,          px:pxTwin},
    {key:"real",  label:"Réel — ce pilote",            color:"#e0573a", ev:real.ev,            tpd:real.tpd,          n:real.n,        px:pxReal},
  ];
  return {c, base, sz, szTxt, tw, real, SRC};
}

// one projection cell: GAIN headline (signed/coloured) + capital atteint, then the two
// uncertainty fourchettes below (muted) — win-rate ±3 % and book-fill 10–100 %, kept
// SEPARATE so each delta reads on its own. `px` = the representative entry price, used to
// turn the ±3 % win-rate band into an EV/$ band (see projRanges).
function projCell(ev, tpd, base, sz, days, px){
  const g = projGain(ev, tpd, base, sz, days);
  if(g==null) return html`<td class="mut">—</td>`;
  const r = projRanges(ev, tpd, base, sz, days, px);
  return html`<td><span class=${"num "+signCls(g)}>${g>=0?"+$":"−$"}${fmt0(Math.abs(g))}</span>
    <div class="cell-sub mut">→ $${fmt0(base+g)}</div>
    ${r ? html`
      <div class="cell-sub mut" title="capital si la réussite des paris dérive de ±3 % (carnet rempli à plein)">⇄ réussite ±3 %&nbsp;: $${fmt0(r.win.lo)} – $${fmt0(r.win.hi)}</div>
      <div class="cell-sub mut" title="capital si le carnet ne remplit que 10–100 % de la mise visée (réussite au centre)">⇄ carnet 10–100 %&nbsp;: $${fmt0(r.fill.lo)} – $${fmt0(r.fill.hi)}</div>` : null}</td>`;
}

// GLOBAL "Tous" view: per running pilot, its REAL projection prominent + a muted PAPER 📄 /
// BACKTEST 📊 gain line; then a fleet TOTAL across all three lenses.
function lensGain(g){ return g==null ? "—" : (g>=0?"+$":"−$")+fmt0(Math.abs(g)); }

// Global-view cell: REAL projection stays prominent (gain + capital + the two uncertainty
// bands), with a muted paper/backtest gain line beneath so each pilot reads against its paper
// twin and its OOS backtest at a glance. Real shows "réel —" while a freshly launched pilot
// has no settled trades yet — paper/backtest already anchor the expectation. Math is the
// shared projGain/projRanges (no drift vs the per-pilot table).
function projCellGlobal(r, d){
  const paper = r.SRC.find(s => s.key === "paper"), bt = r.SRC.find(s => s.key === "bt");
  const g = projGain(r.real.ev, r.real.tpd, r.base, r.sz, d);
  const rg = g == null ? null : projRanges(r.real.ev, r.real.tpd, r.base, r.sz, d, r.sp.avg_price);
  const pg = projGain(paper.ev, paper.tpd, r.base, r.sz, d);
  const bg = projGain(bt.ev,    bt.tpd,    r.base, r.sz, d);
  return html`<td>
    ${g == null ? html`<span class="mut">réel —</span>`
      : html`<span class=${"num "+signCls(g)}>${g>=0?"+$":"−$"}${fmt0(Math.abs(g))}</span>
        <div class="cell-sub mut">→ $${fmt0(r.base+g)}</div>
        ${rg ? html`
          <div class="cell-sub mut" title="capital si la réussite des paris dérive de ±3 % (carnet rempli à plein)">⇄ réussite ±3 %&nbsp;: $${fmt0(rg.win.lo)} – $${fmt0(rg.win.hi)}</div>
          <div class="cell-sub mut" title="capital si le carnet ne remplit que 10–100 % de la mise visée (réussite au centre)">⇄ carnet 10–100 %&nbsp;: $${fmt0(rg.fill.lo)} – $${fmt0(rg.fill.hi)}</div>` : null}`}
    <div class="cell-sub" title="gain projeté aux mêmes réglages de mise — papier (même strat) · backtest OOS">
      <span style=${`color:${paper.color}`}>📄 ${lensGain(pg)}</span> · <span style=${`color:${bt.color}`}>📊 ${lensGain(bg)}</span></div>
  </td>`;
}

function GlobalProjTable({pilots, all}){
  const data = pilots.map(sp => ({sp, ...pilotProj(sp, all)}));
  // fleet TOTAL per lens = sum of each pilot's projected gain at that lens/horizon.
  const sumLens = (key, d) => data.reduce((a, r) => {
    const s = r.SRC.find(x => x.key === key);
    return a + (projGain(s.ev, s.tpd, r.base, r.sz, d) || 0);
  }, 0);
  const totals      = PROJ_H.map(([d]) => sumLens("real",  d));
  const totalsPaper = PROJ_H.map(([d]) => sumLens("paper", d));
  const totalsBt    = PROJ_H.map(([d]) => sumLens("bt",    d));
  // fleet capital bands per horizon = sum of each pilot's two fourchettes (win-rate, fill).
  const totRanges = PROJ_H.map(([d]) => data.reduce((a,r) => {
    const rr = projRanges(r.real.ev, r.real.tpd, r.base, r.sz, d, r.sp.avg_price);
    return rr ? {win:  {lo: a.win.lo + rr.win.lo,  hi: a.win.hi + rr.win.hi},
                 fill: {lo: a.fill.lo + rr.fill.lo, hi: a.fill.hi + rr.fill.hi}} : a;
  }, {win:{lo:0,hi:0}, fill:{lo:0,hi:0}}));
  const anyReal = data.some(r => projGain(r.real.ev, r.real.tpd, r.base, r.sz, 1) != null);
  return html`
    <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>pilote — <span style="color:#e0573a">réel</span> · <span style="color:#e8c264">📄 paper</span> · <span style="color:#a99c80">📊 backtest</span></th>${PROJ_H.map(h=>html`<th>${h[1]}</th>`)}</tr></thead>
      <tbody>
        ${data.map(r => html`<tr>
          <td><span class="cell-name"><span class="g">${glyphOf(r.c.strategy||"")}</span> ${cfgLabel(r.c)}</span>
            <div class="cell-sub mut">avantage/$ réel ${r.real.ev!=null?(r.real.ev>=0?"+":"")+r.real.ev.toFixed(4):"—"} · ${r.real.tpd!=null?r.real.tpd.toFixed(1)+" p/j":"—"} · n=${r.real.n} · ${r.szTxt}</div></td>
          ${PROJ_H.map(([d]) => projCellGlobal(r, d))}
        </tr>`)}
        ${data.length ? html`<tr><td style="border-top:2px solid var(--line-2)"><b>TOTAL — flotte</b></td>
          ${PROJ_H.map((h, i) => html`<td style="border-top:2px solid var(--line-2)">
            ${anyReal ? html`<span class=${"num "+signCls(totals[i])}><b>${totals[i]>=0?"+$":"−$"}${fmt0(Math.abs(totals[i]))}</b></span>
              <div class="cell-sub mut" title="capital flotte si la réussite dérive de ±3 % (carnet plein)">⇄ réussite ±3 %&nbsp;: $${fmt0(totRanges[i].win.lo)} – $${fmt0(totRanges[i].win.hi)}</div>
              <div class="cell-sub mut" title="capital flotte si le carnet ne remplit que 10–100 % de la mise">⇄ carnet 10–100 %&nbsp;: $${fmt0(totRanges[i].fill.lo)} – $${fmt0(totRanges[i].fill.hi)}</div>`
            : html`<span class="mut">réel —</span>`}
            <div class="cell-sub"><span style="color:#e8c264">📄 <b>${lensGain(totalsPaper[i])}</b></span> · <span style="color:#a99c80">📊 <b>${lensGain(totalsBt[i])}</b></span></div></td>`)}</tr>` : null}
      </tbody>
    </table></div>
    <div class="note-box" style="margin-top:14px">Vue <b>flotte</b> : une ligne par pilote en cours. Gros chiffre =
      <b style="color:#e0573a">réel</b> (l'avantage/$ réalisé par ce pilote, argent réel), <b>composé</b> à sa
      pondération (mise = % du capital, plafonnée au carnet) ; « → » = capital atteint ; « <b>⇄</b> » = fourchette
      du capital (réussite <b>±3 %</b> → ±0,03/prix · remplissage <b>10–100 %</b> du carnet). Dessous, aux <b>mêmes
      réglages de mise</b> : <b style="color:#e8c264">📄 paper</b> = gain projeté sur l'avantage/$ du run <b>papier</b>
      de la même strat · <b style="color:#a99c80">📊 backtest</b> = sur l'avantage/$ <b>OOS</b> (passé). Le réel affiche
      « — » tant qu'un pilote frais n'a pas de trades réglés (paper/backtest l'ancrent déjà). ⚠ Les pilotes
      <b>partagent un wallet</b> : le TOTAL additionne des projections à capitaux supposés indépendants (à affiner) ;
      composer un edge fin sans décote slippage/régime <b>gonfle vite</b> — un plafond optimiste, pas une promesse.
      Onglet d'un pilote = ses 3 lentilles en détail.</div>`;
}

// PER-PILOT view: the 3 lenses (paper · backtest · réel) × horizons.
function PilotProjTable({sp, all}){
  const {SRC, base, sz, szTxt, tw, real, c} = pilotProj(sp, all);
  return html`
    <div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>source</th>${PROJ_H.map(h=>html`<th>${h[1]}</th>`)}</tr></thead>
      <tbody>${SRC.map(s => html`<tr>
        <td><span style=${`color:${s.color}`}>●</span> ${s.label}
          <div class="cell-sub mut">avantage/$ ${s.ev!=null?(s.ev>=0?"+":"")+s.ev.toFixed(4):"—"}
            · ${s.tpd!=null?s.tpd.toFixed(1)+" paris/j":"—"}${s.n!=null?` · n=${s.n}`:""}</div></td>
        ${PROJ_H.map(([d]) => projCell(s.ev, s.tpd, base, sz, d, s.px))}
      </tr>`)}</tbody>
    </table></div>
    <div class="note-box" style="margin-top:14px">Gros chiffre = le <b>gain</b> projeté ; « → » = le capital atteint (base + gain) ;
      « <b>⇄</b> » = la <b>fourchette du capital</b> : probabilité de réussite des paris <b>±3 %</b> (décale l'avantage/$
      de ±0,03/prix) <b>et</b> remplissage de <b>10 à 100 %</b> de la mise visée selon la profondeur du carnet.
      Trois lentilles, <b>aux réglages de mise de CE pilote</b> (${szTxt}) :
      <b style="color:#e8c264">Paper</b> = avantage/$ du run <b>papier</b> de la même strat ·
      <b style="color:#a99c80">Backtest</b> = avantage/$ <b>OOS</b> (passé) ·
      <b style="color:#e0573a">Réel</b> = avantage/$ <b>réalisé par ce pilote</b> (argent réel).
      Projection ${c.weighted ? "<b>composée</b> (la mise grossit avec le capital, plafonnée au carnet)" : "<b>linéaire</b> (mise fixe — ne compose pas)"},
      avantage supposé constant, <b>sans décote slippage/régime</b> (à ajuster) — un plafond optimiste.
      Sous ~150 paris c'est du bruit (réel n=${real.n}, paper n=${tw.n_settled||0}).</div>`;
}

// The section on the pilot page: a global "Tous" table + one tab per running pilot.
function PilotProjections({pilots, all}){
  const running = pilots.filter(p => p.alive);
  const [tab, setTab] = useState("all");
  if(!running.length)
    return html`<${Panel} title="Projection de gain"><${Empty}>Aucun pilote en cours — lance ou arme un pilote pour voir la projection.</${Empty}></${Panel}>`;
  const ids = running.map(p => p.id);
  const cur = (tab === "all" || ids.includes(tab)) ? tab : "all";
  const sp = cur !== "all" ? running.find(p => p.id === cur) : null;
  return html`
    <${Panel} title="Projection de gain — pilotes en cours" flush=${true}
      aside=${html`<span class="mut">composé · ${running.length} pilote${running.length>1?"s":""} en cours</span>`}>
      <div class="filters">
        <button class=${"btn sm"+(cur==="all"?" primary":"")} onClick=${()=>setTab("all")}>Tous</button>
        ${running.map(p => html`<button class=${"btn sm"+(cur===p.id?" primary":"")} onClick=${()=>setTab(p.id)}>
          <span class="g" style="margin-right:5px">${glyphOf((p.status.config||{}).strategy||"")}</span>${cfgLabel(p.status.config||{})}</button>`)}
      </div>
      <div style="padding:16px 18px">
        ${cur === "all"
          ? html`<${GlobalProjTable} pilots=${running} all=${all} />`
          : html`<${PilotProjTable} sp=${sp} all=${all} />`}
      </div>
    </${Panel}>`;
}
