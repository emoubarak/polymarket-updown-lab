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
// $/day norm. shrinkage strength: pseudo-count of backtest "trades" mixed into the live
// edge. At n=K the weighted edge is half-live/half-prior; n≫K → ~all live. K=50 is
// conservative early (a lucky n=18 streak can't top the board) yet trusts the live edge
// by the n≥150 verdict threshold (150/200 = 75% live).
const SHRINK_K = 50;

// `all` (/all) and `pilot` (/pilot-data) are polled once in App and shared.
export function Overview({all, pilot, go}){
  // Trial-table filters — coin / strat / frame; we segment by token,
  // brain and window (the race scales with the breadth extension).
  const [fCoin, setFC] = useState(SAVED.coin);
  const [fBrain, setFB] = useState(SAVED.brain);
  const [fFrame, setFF] = useState(SAVED.frame);
  const setFCoin = v => { SAVED.coin = v; setFC(v); };
  const setFBrain = v => { SAVED.brain = v; setFB(v); };
  const setFFrame = v => { SAVED.frame = v; setFF(v); };
  if(!all) return html`<div class="empty">Loading the race…</div>`;

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
    // $/day = throughput, not EV/$: per-trade EV hides frequency. real = read straight
    // off the equity curve (= $/h × 24); bt = projected (bt EV/$ × bt bets/day ×
    // stake). Makers have no prior of their own → borrow the taker twin's
    // (same "gate" rule as the bt Edge column).
    const dpdReel = (m.per_hour||0) * 24;
    let evP = m.ev_prior, tpdP = m.tpd_prior, dpdGate = false;
    if ((evP==null || tpdP==null) && isMakerName(n)) {
      const tw = (all[takerTwin(n)]||{}).metrics || {};
      if (tw.ev_prior!=null && tw.tpd_prior!=null) { evP=tw.ev_prior; tpdP=tw.tpd_prior; dpdGate=true; }
    }
    const dpdBt = (evP!=null && tpdP!=null) ? evP*tpdP*stk : null;
    // NORMALIZED $/day = REAL edge/$ × ESTIMATED frequency × stake. We keep the real
    // per-bet edge but replace the observed frequency (= age/burst noise in real $/day)
    // with a stable estimate: backtest tpd (maker → taker twin, same gate = same
    // cadence), else the runner's observed bets/day (marked "obs"). Decorrelates daily
    // throughput from run length → fair comparison across strategies.
    let tpdEst = m.tpd_prior, tpdObs = false;
    if (tpdEst==null && isMakerName(n)) {
      const tw = (all[takerTwin(n)]||{}).metrics || {};
      if (tw.tpd_prior!=null) tpdEst = tw.tpd_prior;
    }
    if (tpdEst==null) { tpdEst = (m.tpd_live>0) ? m.tpd_live : null; tpdObs = true; }
    // CONFIDENCE-WEIGHT the realized edge toward the backtest prior (evP, maker→twin;
    // 0 if no backtest) by sample size: ev_w = (n·ev_live + K·ev_bt)/(n+K). A young/lucky
    // runner leans on the modest prior; n≥~150 trusts live → $/day norm. ranks the best strat
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
  const plMode = !pl.alive ? "stopped" : pl.status?.mode==="live" ? "🔴 live armed" : "trial (dry-run)";
  const plCfg = pl.status?.config;
  const plSub = pl.alive && plCfg
    ? `${plCfg.strategy} ${String(plCfg.underlying||"btc").toUpperCase()} ${plCfg.interval} · ${pl.n_trades||0} bets`
    : "not armed — see The pilot";

  // distinct filter values present in the lineup (order = lineup order, deduped)
  const coins  = [...new Set(rows.map(r=>partsOf(r.n).token).filter(Boolean))];
  const brains = [...new Set(rows.map(r=>partsOf(r.n).brain).filter(Boolean))];
  const frames = ["5m","15m"].filter(f=>rows.some(r=>partsOf(r.n).frame===f));
  const shown = rows.filter(r=>{
    const p = partsOf(r.n);
    return (fCoin==="all"||p.token===fCoin) && (fBrain==="all"||p.brain===fBrain)
        && (fFrame==="all"||p.frame===fFrame);
  });
  // ONE page-level filter governing every detail panel below (leaderboard, capital,
  // activity) — they all read `shown`. `filtered` (view reduced) drives the uniform
  // "filtered" asides; `anyFilter` (a selector left of "all") drives the reset button.
  const filtered = shown.length < rows.length;
  const anyFilter = fCoin!=="all" || fBrain!=="all" || fFrame!=="all";
  const filtNote = filtered ? `${shown.length}/${rows.length} · filtered` : null;
  const seg = (cur, val, set, label) => html`
    <button class=${"btn sm"+(cur===val?" primary":"")} onClick=${()=>set(val)}>${label}</button>`;
  const filterBar = html`
    <div class="filters page" role="group" aria-label="Filter the race">
      <span class="ftitle">Filter</span>
      <span class="flab">Coin</span>${seg(fCoin,"all",setFCoin,"all")}
      ${coins.map(c=>seg(fCoin,c,setFCoin,c.toUpperCase()))}
      <span class="flab">Strat</span>${seg(fBrain,"all",setFBrain,"all")}
      ${brains.map(b=>seg(fBrain,b,setFBrain,b))}
      <span class="flab">Frame</span>${seg(fFrame,"all",setFFrame,"all")}
      ${frames.map(f=>seg(fFrame,f,setFFrame,f))}
      ${anyFilter ? html`<button class="btn sm reset" onClick=${()=>{setFCoin("all");setFBrain("all");setFFrame("all");}}>reset</button>` : null}
    </div>`;

  const cols = [
    {key:"name", label:"Strategy", sortable:true, get:r=>nameOf(r.n),
      cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.n)}</span><span class="lead">${nameOf(r.n)}</span></span>`},
    {key:"verdict", label:"Verdict", sortable:true, get:r=>({wait:0,none:1,trial:2,proven:3}[r.v.key]),
      cell:r=>html`<${Seal} v=${r.v} />`},
    {key:"live", label:"Live P&L", sortable:true, get:r=>r.live, title:"Realized live P&L · paper money (≠ real pilot)",
      cell:r=>html`<${Money} v=${r.live} signed=${true} />`},
    {key:"ev", label:"Real edge/$", sortable:true, get:r=>r.m.ev_live||0, title:"Realized live EV/$ (paper money)",
      cell:r=>r.m.n_settled
        ? html`<span class=${"num "+signCls(r.m.ev_live)}>${(r.m.ev_live>=0?"+":"")+r.m.ev_live.toFixed(4)}</span>`
        : html`<span class="mut">—</span>`},
    {key:"ev_bt", label:"Bt edge/$", sortable:true, get:r=>r.ebk?r.ebk.ev:0,
      title:"EV/$ measured on the past (OOS backtest) · gate = inherited from the taker twin",
      cell:r=>r.ebk
        ? html`<span class=${"num "+signCls(r.ebk.ev)}>${(r.ebk.ev>=0?"+":"")+r.ebk.ev.toFixed(4)}</span>${r.bkGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"win", label:"Real win / price", sortable:true, get:r=>r.m.win_rate||0, title:"live win rate vs price paid (the tripwire)",
      cell:r=>r.m.n_settled
        ? html`<span class=${"num "+(r.m.win_rate>r.m.avg_price?"pos":"neg")}>${pct(r.m.win_rate)}</span>
               <span class="mut"> / ${pct(r.m.avg_price)}</span>`
        : html`<span class="mut">—</span>`},
    {key:"win_bt", label:"Bt win / price", sortable:true, get:r=>r.ebk?r.ebk.win:0,
      title:"win rate vs price paid in the backtest (OOS) · gate = inherited from the taker twin",
      cell:r=>r.ebk
        ? html`<span class=${"num "+(r.ebk.win>r.ebk.px?"pos":"neg")}>${pct(r.ebk.win)}</span>
               <span class="mut"> / ${pct(r.ebk.px)}</span>${r.bkGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"n", label:"Bets", sortable:true, get:r=>r.m.n_settled||0,
      cell:r=>html`<span class="num">${r.m.n_settled||"—"}</span>`},
    {key:"bet", label:"Stake", sortable:true, get:r=>r.cap,
      title:`bet size = ${Math.round(CONFIG.WEIGHT_PCT*100)} % of capital, capped at the coin's book depth (max absorbable stake @+2 ¢)`,
      cell:r=>html`<span class="num">${Math.round(CONFIG.WEIGHT_PCT*100)} % <span class="mut">· max</span> $${fmt0(r.cap)}</span>`},
    {key:"dpd", label:"$/day real", sortable:true, get:r=>r.dpdReel||0,
      title:"realized throughput per day = real $/h × 24 (real EV/$ × observed bets/day × stake) — noisy while the runner is young (extrapolates elapsed time)",
      cell:r=>r.m.n_settled?html`<${Money} v=${r.dpdReel} signed=${true} />`:html`<span class="mut">—</span>`},
    {key:"dpd_norm", label:"$/day norm.", sortable:true, get:r=>r.dpdNorm==null?-1e9:r.dpdNorm,
      title:"NORMALIZED & WEIGHTED daily throughput = confidence-weighted edge × estimated frequency × stake. The real edge is pulled toward the backtest while n is small (shrinkage K=50), then released once n≥~150 → ranks the BEST strat overall, not a lucky streak. Frequency = backtest (maker = taker twin), else observed (“obs”). No backtest: edge pulled toward 0.",
      cell:r=>r.dpdNorm!=null
        ? html`<${Money} v=${r.dpdNorm} signed=${true} />${r.tpdObs?html`<span class="mut"> obs</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"dpd_bt", label:"$/day bt", sortable:true, get:r=>r.dpdBt==null?-1e9:r.dpdBt,
      title:"projected throughput per day in the backtest = bt EV/$ × bt bets/day × stake · gate = taker twin",
      cell:r=>r.dpdBt!=null
        ? html`<${Money} v=${r.dpdBt} signed=${true} />${r.dpdGate?html`<span class="mut"> gate</span>`:""}`
        : html`<span class="mut">—</span>`},
    {key:"health", label:"Engine", get:r=>r.h.age||0,
      cell:r=>html`<span class="num"><${HealthDot} h=${r.h} /></span>`},
  ];

  // the equity chart honours the SAME coin/strat/frame filters as the leaderboard (with
  // 48 runners, an unfiltered chart is unreadable). `shown` = the filtered rows.
  const series = shown.filter(r=>r.eq).map(r=>({pts:r.eq, color:COLORS[r.i%COLORS.length], name:nameOf(r.n)}));

  return html`
    <div class="page-head">
      <div><h1>Overview</h1>
        <p class="sub">The zlead family — one engine, composable types (n/x/mk) + per-coin entry slot,
          across ${CONFIG.STRATS.length} runners (coins × variants × frames, we accumulate).
          The single judge: over ~150 bets, does the win rate beat the price paid?</p></div>
    </div>

    <div class="kpis">
      <${Kpi} real=${true} k="Pilot — real money" glyph="⚗"
        v=${html`<span class=${signCls(pl.realized_pnl||0)}>${moneyS(pl.realized_pnl||0)}</span>`}
        sub=${plMode + " · " + plSub} />
      <${Kpi} accent="gold" k="Race — live (paper)" glyph="🜍"
        v=${html`<span class=${signCls(totLive)}>${moneyS(totLive)}</span>`}
        sub=${`${CONFIG.STRATS.length} strategies · ${totN} settled bets · paper money`} />
      <${Kpi} accent="verd" k="Proven edge"
        v=${html`<span class="verd">${proven}</span><span class="mut"> / ${CONFIG.STRATS.length}</span>`}
        sub="tripwire cleared (n≥150 & win>price)" num=${false} />
      <${Kpi} accent="merc" k="Best edge/$"
        v=${best ? html`<span class="num">${glyphOf(best.n)} ${nameOf(best.n)}</span>` : "—"}
        sub=${best ? `${(best.m.ev_live>=0?"+":"")+best.m.ev_live.toFixed(4)}/$ · ${best.m.n_settled} bets` : "waiting"} num=${false} />
    </div>

    ${filterBar}

    <${Panel} title="Leaderboard — the trial table" flush=${true}
      aside=${filtNote ? `${filtNote} · click a row` : "click a row for details"}>
      <${DataTable} cols=${cols} rows=${shown} sort=${{key:"live",dir:-1}}
        onRowClick=${r=>go("strat/"+r.n)} empty="No strategy matches the filter." />
    </${Panel}>

    <${Panel} title="Capital — paper money" aside=${`${series.length} curve${series.length>1?"s":""}${filtered?" · filtered":""}`}>
      <${LineChart} series=${series} base=${CONFIG.START} height=${300} />
    </${Panel}>

    <${Panel} title="Activity — real time" flush=${true} aside=${filtNote}>
      <${DataTable} empty=${filtered ? "No strategy matches the filter." : "No telemetry."}
        rows=${shown} sort=${{key:"tau",dir:1}}
        cols=${[
          {key:"name", label:"Strategy", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.n)}</span>${nameOf(r.n)}</span>`},
          {key:"state", label:"Engine", cell:r=>html`<span class="num"><${HealthDot} h=${r.h} /></span>`},
          {key:"fav", label:"Favorite", cell:r=>r.tick?html`<span class="num">${dirLabel(r.tick.fav_side)} @ ${(+r.tick.fav_price).toFixed(2)}</span>`:html`<span class="mut">—</span>`},
          {key:"lead", label:"Lead", sortable:true, get:r=>r.tick?+r.tick.lead_bps:0,
            cell:r=>r.tick?html`<span class=${"num "+(Math.abs(r.tick.lead_bps)<6?"neg":"pos")}>${r.tick.lead_bps>=0?"+":""}${r.tick.lead_bps} bps</span>`:html`<span class="mut">—</span>`},
          {key:"tau", label:"τ left", sortable:true, get:r=>r.tick?+r.tick.tau_min:999,
            cell:r=>r.tick?html`<span class="num">${(+r.tick.tau_min).toFixed(1)} min</span>`:html`<span class="mut">—</span>`},
          {key:"slot", label:"Slot", cell:r=>{ const p=(CONFIG.PRESETS||{})[partsOf(r.n).brain];
            return p?html`<span class="num mut">${effectiveSlot(p, partsOf(r.n).frame, partsOf(r.n).token, CONFIG.COIN_SLOTS)}</span>`:html`<span class="mut">—</span>`; }},
          {key:"eng", label:"In a bet?", cell:r=>r.tick&&r.tick.open_here?html`<${Tag} tone="gold">position</${Tag}>`:html`<span class="mut">—</span>`},
        ]} />
    </${Panel}>

    <${Note}>⚠ All ${CONFIG.STRATS.length} strategies bet the <b>same favorites</b>: this is ONE
      parameterized edge, not ${CONFIG.STRATS.length} independent ones — a bad regime hits them together.
      For real money, we pick one; we don't "diversify" across them.</${Note}>`;
}
