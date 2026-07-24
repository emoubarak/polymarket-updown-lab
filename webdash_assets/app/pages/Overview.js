// Command center — the single screen that answers "how's it going, is anything
// real, where's the real money": KPIs → sortable leaderboard → equity → activity.
import { html, useState } from '../preact.js';
import { CONFIG, bkOf, gatedBk } from '../api.js';
import { glyphOf, nameOf, partsOf, isMakerName, takerTwin, verdict, health, simPnL, moneyS, money, pct, signCls, ageStr, dirLabel, COLORS, effectiveSlot, betMax, effStake, fmt0 } from '../format.js';
import { Kpi, Panel, Seal, Money, HealthDot, Note, Empty, Tag } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';

// filter selection persists across navigation (module-scoped, resets on full reload)
let SAVED = {coin:"all", brain:"all", frame:"all"};
// $/j norm. shrinkage strength: pseudo-count of backtest "trades" mixed into the live
// edge. At n=K the weighted edge is half-live/half-prior; n≫K → ~all live. K=50 is
// conservative early (a lucky n=18 streak can't top the board) yet trusts the live edge
// by the n≥150 verdict threshold (150/200 = 75% live).
const SHRINK_K = 50;

// `all` (/all) and `pilot` (/pilot-data) are polled once in App and shared.
export function Overview({all, pilot, go}){
  // Filtres de la table d'essai — coin / strat / frame ; on segmente par jeton,
  // cerveau et fenêtre (la course scale avec l'extension breadth).
  const [fCoin, setFC] = useState(SAVED.coin);
  const [fBrain, setFB] = useState(SAVED.brain);
  const [fFrame, setFF] = useState(SAVED.frame);
  const setFCoin = v => { SAVED.coin = v; setFC(v); };
  const setFBrain = v => { SAVED.brain = v; setFB(v); };
  const setFFrame = v => { SAVED.frame = v; setFF(v); };
  if(!all) return html`<div class="empty">Chargement de la course…</div>`;

  const rows = CONFIG.STRATS.map((n, i) => {
    const d = all[n] || {}, s = d.state || {}, m = d.metrics || {}, bk = bkOf(n);
    const coin = partsOf(n).token, frame = partsOf(n).frame;
    // Per-(coin,frame) sizing (weighted 10 % of capital, capped at that book's depth). The $
    // projection columns below use the runner's CURRENT effective bet so live $ and projected $
    // are on the same scale (flat $25 would lie now that each coin/frame sizes differently).
    const cap = betMax(coin, frame, CONFIG.COIN_DEPTH, CONFIG.STAKE);
    const capital = (d.equity && d.equity.length) ? d.equity[d.equity.length-1][1]
                                                  : (s.initial||CONFIG.START)+(s.realized_pnl||0);
    const stk = effStake(coin, frame, capital, CONFIG.COIN_DEPTH, CONFIG.WEIGHT_PCT, CONFIG.STAKE);
    const {bk:ebk, gate:bkGate} = gatedBk(n);  // effective bt: own, or maker→taker-twin gate
    // $/jour = débit, pas EV/$ : l'EV par trade cache la fréquence. réel = lu direct
    // sur la courbe d'équité (= $/h × 24) ; bt = projeté (EV/$ bt × paris/jour bt ×
    // mise). Les makers n'ont pas de prior propre → on emprunte celui du jumeau taker
    // (même règle "gate" que la colonne Avantage bt).
    const dpdReel = (m.per_hour||0) * 24;
    let evP = m.ev_prior, tpdP = m.tpd_prior, dpdGate = false;
    if ((evP==null || tpdP==null) && isMakerName(n)) {
      const tw = (all[takerTwin(n)]||{}).metrics || {};
      if (tw.ev_prior!=null && tw.tpd_prior!=null) { evP=tw.ev_prior; tpdP=tw.tpd_prior; dpdGate=true; }
    }
    const dpdBt = (evP!=null && tpdP!=null) ? evP*tpdP*stk : null;
    // $/j NORMALISÉ = edge RÉEL/$ × fréquence ESTIMÉE × mise. On garde l'edge réel par
    // pari mais on remplace la fréquence observée (= bruit d'âge/burst de $/j réel) par
    // une estimée stable : tpd du backtest (maker → jumeau taker, même gate = même
    // cadence), sinon les paris/jour observés du runner (marqués "obs"). Décorrèle le
    // débit journalier de la durée de run → comparaison équitable entre stratégies.
    let tpdEst = m.tpd_prior, tpdObs = false;
    if (tpdEst==null && isMakerName(n)) {
      const tw = (all[takerTwin(n)]||{}).metrics || {};
      if (tw.tpd_prior!=null) tpdEst = tw.tpd_prior;
    }
    if (tpdEst==null) { tpdEst = (m.tpd_live>0) ? m.tpd_live : null; tpdObs = true; }
    // CONFIDENCE-WEIGHT the realized edge toward the backtest prior (evP, maker→twin;
    // 0 if no backtest) by sample size: ev_w = (n·ev_live + K·ev_bt)/(n+K). A young/lucky
    // runner leans on the modest prior; n≥~150 trusts live → $/j norm. ranks the best strat
    // OVERALL, not a noise spike. No backtest → edge pulled toward 0 (earn it live).
    const priorEv = (evP!=null) ? evP : 0;
    const evW = m.n_settled ? (m.n_settled*m.ev_live + SHRINK_K*priorEv)/(m.n_settled + SHRINK_K) : null;
    const dpdNorm = (evW!=null && tpdEst) ? evW*tpdEst*stk : null;
    return {n, i, m, bk, ebk, bkGate, tick:d.tick, v:verdict(m), h:health(d.tick), cap,
      live:(s.realized_pnl||0), sim:simPnL(bk, stk),
      dpdReel, dpdBt, dpdGate, dpdNorm, tpdObs,
      eq:(d.equity && d.equity.length>1) ? d.equity : null};
  });

  const totLive = rows.reduce((a,r)=>a+r.live, 0);
  const totN = rows.reduce((a,r)=>a+(r.m.n_settled||0), 0);
  const proven = rows.filter(r=>r.v.key==="proven").length;
  const best = rows.filter(r=>r.m.n_settled).sort((a,b)=>(b.m.ev_live||-9)-(a.m.ev_live||-9))[0];

  // real-money pilot KPI
  const pl = pilot || {};
  const plMode = !pl.alive ? "arrêté" : pl.status?.mode==="live" ? "🔴 réel armé" : "essai (dry-run)";
  const plCfg = pl.status?.config;
  const plSub = pl.alive && plCfg
    ? `${plCfg.strategy} ${String(plCfg.underlying||"btc").toUpperCase()} ${plCfg.interval} · ${pl.n_trades||0} paris`
    : "non armé — voir Le pilote";

  // distinct filter values present in the lineup (order = lineup order, deduped)
  const coins  = [...new Set(rows.map(r=>partsOf(r.n).token).filter(Boolean))];
  const brains = [...new Set(rows.map(r=>partsOf(r.n).brain).filter(Boolean))];
  const frames = ["5m","15m"].filter(f=>rows.some(r=>partsOf(r.n).frame===f));
  const shown = rows.filter(r=>{
    const p = partsOf(r.n);
    return (fCoin==="all"||p.token===fCoin) && (fBrain==="all"||p.brain===fBrain)
        && (fFrame==="all"||p.frame===fFrame);
  });
  // ONE page-level filter governing every detail panel below (classement, capital,
  // activité) — they all read `shown`. `filtered` (view reduced) drives the uniform
  // "filtré" asides ; `anyFilter` (a selector left of "all") drives the reset button.
  const filtered = shown.length < rows.length;
  const anyFilter = fCoin!=="all" || fBrain!=="all" || fFrame!=="all";
  const filtNote = filtered ? `${shown.length}/${rows.length} · filtré` : null;
  const seg = (cur, val, set, label) => html`
    <button class=${"btn sm"+(cur===val?" primary":"")} onClick=${()=>set(val)}>${label}</button>`;
  const filterBar = html`
    <div class="filters page" role="group" aria-label="Filtrer la course">
      <span class="ftitle">Filtre</span>
      <span class="flab">Coin</span>${seg(fCoin,"all",setFCoin,"tous")}
      ${coins.map(c=>seg(fCoin,c,setFCoin,c.toUpperCase()))}
      <span class="flab">Strat</span>${seg(fBrain,"all",setFBrain,"toutes")}
      ${brains.map(b=>seg(fBrain,b,setFBrain,b))}
      <span class="flab">Frame</span>${seg(fFrame,"all",setFFrame,"tous")}
      ${frames.map(f=>seg(fFrame,f,setFFrame,f))}
      ${anyFilter ? html`<button class="btn sm reset" onClick=${()=>{setFCoin("all");setFBrain("all");setFFrame("all");}}>réinitialiser</button>` : null}
    </div>`;

  const cols = [
    {key:"name", label:"Stratégie", sortable:true, get:r=>nameOf(r.n),
      cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.n)}</span><span class="lead">${nameOf(r.n)}</span></span>`},
    {key:"verdict", label:"Verdict", sortable:true, get:r=>({wait:0,none:1,trial:2,proven:3}[r.v.key]),
      cell:r=>html`<${Seal} v=${r.v} />`},
    {key:"live", label:"P&L direct", sortable:true, get:r=>r.live, title:"P&L réalisé en direct · argent fictif (≠ pilote réel)",
      cell:r=>html`<${Money} v=${r.live} signed=${true} />`},
    {key:"ev", label:"Avantage réel/$", sortable:true, get:r=>r.m.ev_live||0, title:"EV/$ réalisé en direct (argent fictif)",
      cell:r=>r.m.n_settled
        ? html`<span class=${"num "+signCls(r.m.ev_live)}>${(r.m.ev_live>=0?"+":"")+r.m.ev_live.toFixed(4)}</span>`
        : html`<span class="mut">—</span>`},
    {key:"ev_bt", label:"Avantage bt/$", sortable:true, get:r=>r.ebk?r.ebk.ev:0,
      title:"EV/$ mesuré sur le passé (backtest OOS) · gate = hérité du jumeau taker",
      cell:r=>r.ebk
        ? html`<span class=${"num "+signCls(r.ebk.ev)}>${(r.ebk.ev>=0?"+":"")+r.ebk.ev.toFixed(4)}</span>${r.bkGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"win", label:"Win réel / prix", sortable:true, get:r=>r.m.win_rate||0, title:"réussite vs prix payé en direct (le tripwire)",
      cell:r=>r.m.n_settled
        ? html`<span class=${"num "+(r.m.win_rate>r.m.avg_price?"pos":"neg")}>${pct(r.m.win_rate)}</span>
               <span class="mut"> / ${pct(r.m.avg_price)}</span>`
        : html`<span class="mut">—</span>`},
    {key:"win_bt", label:"Win bt / prix", sortable:true, get:r=>r.ebk?r.ebk.win:0,
      title:"réussite vs prix payé au backtest (OOS) · gate = hérité du jumeau taker",
      cell:r=>r.ebk
        ? html`<span class=${"num "+(r.ebk.win>r.ebk.px?"pos":"neg")}>${pct(r.ebk.win)}</span>
               <span class="mut"> / ${pct(r.ebk.px)}</span>${r.bkGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"n", label:"Paris", sortable:true, get:r=>r.m.n_settled||0,
      cell:r=>html`<span class="num">${r.m.n_settled||"—"}</span>`},
    {key:"bet", label:"Mise", sortable:true, get:r=>r.cap,
      title:`taille de pari = ${Math.round(CONFIG.WEIGHT_PCT*100)} % du capital, plafonnée à la profondeur du carnet du coin (mise max absorbable @+2 ¢)`,
      cell:r=>html`<span class="num">${Math.round(CONFIG.WEIGHT_PCT*100)} % <span class="mut">· max</span> $${fmt0(r.cap)}</span>`},
    {key:"dpd", label:"$/j réel", sortable:true, get:r=>r.dpdReel||0,
      title:"débit réalisé par jour = $/h réel × 24 (EV/$ réel × paris/jour observés × mise) — bruité tant que le runner est jeune (extrapole le temps écoulé)",
      cell:r=>r.m.n_settled?html`<${Money} v=${r.dpdReel} signed=${true} />`:html`<span class="mut">—</span>`},
    {key:"dpd_norm", label:"$/j norm.", sortable:true, get:r=>r.dpdNorm==null?-1e9:r.dpdNorm,
      title:"débit journalier NORMALISÉ & PONDÉRÉ = edge confiance-pondéré × fréquence estimée × mise. L'edge réel est tiré vers le backtest tant que n est faible (shrinkage K=50), puis libéré quand n≥~150 → classe la MEILLEURE strat overall, pas un coup de chance. Fréquence = backtest (maker = jumeau taker), sinon observée (« obs »). Sans backtest : edge tiré vers 0.",
      cell:r=>r.dpdNorm!=null
        ? html`<${Money} v=${r.dpdNorm} signed=${true} />${r.tpdObs?html`<span class="mut"> obs</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"dpd_bt", label:"$/j bt", sortable:true, get:r=>r.dpdBt==null?-1e9:r.dpdBt,
      title:"débit projeté par jour au backtest = EV/$ bt × paris/jour bt × mise · gate = jumeau taker",
      cell:r=>r.dpdBt!=null
        ? html`<${Money} v=${r.dpdBt} signed=${true} />${r.dpdGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"health", label:"Moteur", get:r=>r.h.age||0,
      cell:r=>html`<span class="num"><${HealthDot} h=${r.h} /></span>`},
  ];

  // the equity chart honours the SAME coin/strat/frame filters as the leaderboard (with
  // 48 runners, an unfiltered chart is unreadable). `shown` = the filtered rows.
  const series = shown.filter(r=>r.eq).map(r=>({pts:r.eq, color:COLORS[r.i%COLORS.length], name:nameOf(r.n)}));

  return html`
    <div class="page-head">
      <div><h1>Vue d'ensemble</h1>
        <p class="sub">La famille zlead — un moteur, des types composables (n/x/mk) + créneau d'entrée par coin,
          en ${CONFIG.STRATS.length} runners (coins × variantes × frames, on accumule).
          Le juge unique : sur ~150 paris, la réussite bat-elle le prix payé ?</p></div>
    </div>

    <div class="kpis">
      <${Kpi} real=${true} k="Pilote — argent réel" glyph="⚗"
        v=${html`<span class=${signCls(pl.realized_pnl||0)}>${moneyS(pl.realized_pnl||0)}</span>`}
        sub=${plMode + " · " + plSub} />
      <${Kpi} accent="gold" k="Course — en direct (papier)" glyph="🜍"
        v=${html`<span class=${signCls(totLive)}>${moneyS(totLive)}</span>`}
        sub=${`${CONFIG.STRATS.length} stratégies · ${totN} paris réglés · argent fictif`} />
      <${Kpi} accent="verd" k="Avantage prouvé"
        v=${html`<span class="verd">${proven}</span><span class="mut"> / ${CONFIG.STRATS.length}</span>`}
        sub="tripwire franchi (n≥150 & win>prix)" num=${false} />
      <${Kpi} accent="merc" k="Meilleur avantage/$"
        v=${best ? html`<span class="num">${glyphOf(best.n)} ${nameOf(best.n)}</span>` : "—"}
        sub=${best ? `${(best.m.ev_live>=0?"+":"")+best.m.ev_live.toFixed(4)}/$ · ${best.m.n_settled} paris` : "en attente"} num=${false} />
    </div>

    ${filterBar}

    <${Panel} title="Classement — la table d'essai" flush=${true}
      aside=${filtNote ? `${filtNote} · clique une ligne` : "clique une ligne pour le détail"}>
      <${DataTable} cols=${cols} rows=${shown} sort=${{key:"live",dir:-1}}
        onRowClick=${r=>go("strat/"+r.n)} empty="Aucune stratégie ne correspond au filtre." />
    </${Panel}>

    <${Panel} title="Capital — argent fictif" aside=${`${series.length} courbe${series.length>1?"s":""}${filtered?" · filtré":""}`}>
      <${LineChart} series=${series} base=${CONFIG.START} height=${300} />
    </${Panel}>

    <${Panel} title="Activité — temps réel" flush=${true} aside=${filtNote}>
      <${DataTable} empty=${filtered ? "Aucune stratégie ne correspond au filtre." : "Pas de télémétrie."}
        rows=${shown} sort=${{key:"tau",dir:1}}
        cols=${[
          {key:"name", label:"Stratégie", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.n)}</span>${nameOf(r.n)}</span>`},
          {key:"state", label:"Moteur", cell:r=>html`<span class="num"><${HealthDot} h=${r.h} /></span>`},
          {key:"fav", label:"Favori", cell:r=>r.tick?html`<span class="num">${dirLabel(r.tick.fav_side)} @ ${(+r.tick.fav_price).toFixed(2)}</span>`:html`<span class="mut">—</span>`},
          {key:"lead", label:"Lead", sortable:true, get:r=>r.tick?+r.tick.lead_bps:0,
            cell:r=>r.tick?html`<span class=${"num "+(Math.abs(r.tick.lead_bps)<6?"neg":"pos")}>${r.tick.lead_bps>=0?"+":""}${r.tick.lead_bps} bps</span>`:html`<span class="mut">—</span>`},
          {key:"tau", label:"τ restant", sortable:true, get:r=>r.tick?+r.tick.tau_min:999,
            cell:r=>r.tick?html`<span class="num">${(+r.tick.tau_min).toFixed(1)} min</span>`:html`<span class="mut">—</span>`},
          {key:"slot", label:"Créneau", cell:r=>{ const p=(CONFIG.PRESETS||{})[partsOf(r.n).brain];
            return p?html`<span class="num mut">${effectiveSlot(p, partsOf(r.n).frame, partsOf(r.n).token, CONFIG.COIN_SLOTS)}</span>`:html`<span class="mut">—</span>`; }},
          {key:"eng", label:"Engagé ?", cell:r=>r.tick&&r.tick.open_here?html`<${Tag} tone="gold">position</${Tag}>`:html`<span class="mut">—</span>`},
        ]} />
    </${Panel}>

    <${Note}>⚠ Les ${CONFIG.STRATS.length} stratégies parient les <b>mêmes favoris</b> : c'est UN edge
      paramétré, pas ${CONFIG.STRATS.length} indépendants — un mauvais régime les touche ensemble.
      Pour l'argent réel, on en choisit une, on ne « diversifie » pas entre elles.</${Note}>`;
}
