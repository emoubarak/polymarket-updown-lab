// Rewards-MM — the std0 liquidity-rewards harvester cockpit. A SEPARATE control plane from
// "The pilot" (different engine run_rewardmm.py, different telemetry: two-sided inventory
// Up/Down, maker fills, the rebate, marked P&L vs cash) so it can never touch the armed zlead
// pilots. Same shared real wallet ⇒ REAL arming is real money. The REAL on-chain path is still
// QUOTA-BOUND until the Gnosis Safe lands (research/std0/NEXT-STEPS.md) — DRY runs cleanly now.
import { html, useState } from '../preact.js';
import { CONFIG, usePoll, postMM } from '../api.js';
import { money, moneyS, signCls, timeOf, fmt0 } from '../format.js';
import { Kpi, Panel, Tag, Empty, Money, Note } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';

// Defaults track run_rewardmm.py; spot-anchor on + btc-5m = the research-recommended target
// (research/std0/NEXT-STEPS.md). Advanced knobs hidden behind a toggle to keep the form sane.
const DEFAULTS = {underlying:"btc", interval:"5m", mint_usd:15, clip:5, quote_dist:0.01,
  spot_anchor:true, beta:0.5, back_off:0, max_inv:10, min_quote:0.30, max_quote:0.70,
  recenter_eps:0.01, flatten_buf:45, kill_loss:8, poll:3};

const frameOpts = () => (CONFIG.FRAMES && CONFIG.FRAMES.length) ? CONFIG.FRAMES : ["5m","15m"];
const coinOpts  = () => ((CONFIG.COINS && CONFIG.COINS.length) ? CONFIG.COINS
  : ["btc","eth","sol","xrp","doge","bnb"]).map(k => [k, k.toUpperCase()]);

const cfgLabel = c => `${String(c.underlying||"").toUpperCase()} ${c.interval}`;
const modeOf = r => !r.alive ? "stopped" : (r.status.mode==="live" ? "🔴 REAL" : "DRY-RUN");
const isLive = r => r.alive && r.status.mode==="live";
// neutrality reads as the std0 invariant: held Up vs Down, Δ should hover ≈ 0.
const neutTxt = r => `${fmt0(r.inv_up)}/${fmt0(r.inv_dn)}${r.neutrality?` (Δ${r.neutrality>0?"+":""}${r.neutrality})`:" (Δ0)"}`;

export function MM(){
  const d = usePoll("/mm-data", 6000).data;
  const [cfg, setCfg] = useState(DEFAULTS);
  const [selected, setSelected] = useState(null);
  const [adv, setAdv] = useState(false);
  const set = (k,v) => setCfg(o => ({...o, [k]: v}));

  async function act(a, id){
    if(a === "start_live"){
      if(!confirm(`⚠ ARM THE REWARDS-MM FOR REAL\n\nREAL two-sided maker orders + an on-chain MINT `
        + `will be sent to Polymarket:\nrewardMM · ${String(cfg.underlying).toUpperCase()} ${cfg.interval} `
        + `· mint $${cfg.mint_usd} · clip ${cfg.clip}\n\n`
        + `⚠ The REAL path is still EXPERIMENTAL and QUOTA-BOUND until the Gnosis Safe is deployed `
        + `(relayer merges can stall). Net-positive only at large scale. Small stake.\n\n`
        + `Money you can afford to lose. Confirm arming?`)) return;
    }
    if(a === "resume" && id){
      const r = (d.runners||[]).find(x => x.id === id);
      if(r && r.status.armed_mode === "live"
        && !confirm(`⚠ RESUME FOR REAL\n\n${id} restarts with real money. Confirm resume?`)) return;
    }
    if(a === "forget" && !confirm("Remove this runner from the list?\nIts on-disk state is kept (relaunching picks it back up).")) return;

    let body;
    if(a === "stop" || a === "resume" || a === "forget"){
      body = new URLSearchParams({action:a, id}).toString();
    } else {
      body = new URLSearchParams({action:a, underlying:cfg.underlying, interval:cfg.interval,
        mint_usd:cfg.mint_usd, clip:cfg.clip, quote_dist:cfg.quote_dist,
        spot_anchor:cfg.spot_anchor?"1":"0", beta:cfg.beta, back_off:cfg.back_off,
        max_inv:cfg.max_inv, min_quote:cfg.min_quote, max_quote:cfg.max_quote,
        recenter_eps:cfg.recenter_eps, flatten_buf:cfg.flatten_buf,
        kill_loss:cfg.kill_loss, poll:cfg.poll}).toString();
    }
    try {
      const r = await postMM(body);
      alert(r.msg || (r.ok ? "ok" : "failed"));
    } catch(e){ alert("network error: " + e); }
  }

  if(!d) return html`<div class="empty">Loading the rewards-MM…</div>`;
  const runners = d.runners || [];
  const agg = d.agg || {};
  const sr = runners.find(r => r.id === selected) || runners[0] || null;

  const sel = (key, opts) => html`<select class="ctl" value=${cfg[key]}
    onChange=${e=>set(key, e.target.value)}>
    ${opts.map(o => { const [v,l] = Array.isArray(o)?o:[o,o]; return html`<option value=${v}>${l}</option>`; })}</select>`;
  const num = (key, step=1) => html`<input class="ctl" type="number" step=${step} value=${cfg[key]}
    onInput=${e=>set(key, +e.target.value)} style="width:96px" />`;

  const cols = [
    {key:"mode", label:"State", cell:r=>html`<${Tag} tone=${isLive(r)?"real":""}>${modeOf(r)}</${Tag}>${r.killed?html` <${Tag} tone="bad">🛑 kill</${Tag}>`:r.alive&&!r.fresh?html` <span class="mut" title="snapshot stale > 60 s">⚠</span>`:null}`},
    {key:"id", label:"Market", cell:r=>html`<span class="cell-name">${sr&&sr.id===r.id?html`<span class="g">▸</span>`:null}
      <span class="g">⚒</span> ${cfgLabel(r.status.config||{})}</span>`},
    {key:"mark", label:"Marked P&L", sortable:true, get:r=>r.mark, cell:r=>html`<${Money} v=${r.mark} signed=${true} />`},
    {key:"rebate", label:"Rebate≈", sortable:true, get:r=>r.rebate, cell:r=>html`<span class="num">${money(r.rebate)}</span>`},
    {key:"fills", label:"Fills", sortable:true, get:r=>r.fills, cell:r=>html`<span class="num">${r.fills}</span>`},
    {key:"neut", label:"Inv Up/Dn", cell:r=>html`<span class=${"num "+(Math.abs(r.neutrality)>r.minted*0.5+1?"neg":"")}>${neutTxt(r)}</span>`},
    {key:"resting", label:"Quotes", cell:r=>html`<span class="num">${r.resting}</span>`},
    {key:"act", label:"", cell:r=>{ const stop = e=>e.stopPropagation();
      return r.alive
        ? html`<button class="btn sm" onClick=${e=>{stop(e); act("stop", r.id);}}>⏸ Pause</button>`
        : html`<span class="row-actions" style="display:inline-flex;gap:6px">
            <button class="btn sm ${r.status.armed_mode==="live"?"danger":""}" onClick=${e=>{stop(e); act("resume", r.id);}}>▶ Resume</button>
            <button class="btn sm danger" onClick=${e=>{stop(e); act("forget", r.id);}}>✕ Remove</button></span>`;
    }},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">⚒</span>Rewards-MM — liquidity harvester (std0)</h1>
        <p class="sub">Tight two-sided maker around the mid, delta-neutral, redeem at settlement. The income is the
          maker <b>rebate</b> (≈0.014·Σp(1−p)·shares), not the spread. ${runners.length} runner${runners.length>1?"s":""}
          (${agg.n_real||0} on real money).</p></div>
      <div class="head-aside">${d.armed_env ? html`<${Tag} tone="real">key ready</${Tag}>` : html`<${Tag}>no key</${Tag}>`}
        ${d.stale ? html` <${Tag} tone="bad" title=${`AWS engine unreachable for ${d.stale_age}s`}>feed stale ${d.stale_age}s</${Tag}>`:null}</div>
    </div>

    ${(agg.n_real > 0) ? html`<div class="kpis" style="margin-bottom:16px">
      <${Kpi} real=${true} k="Marked P&L — real runners"
        v=${html`<span class=${signCls(agg.mark)}>${moneyS(agg.mark)}</span>`}
        sub=${`${agg.n_real} real runner${agg.n_real>1?"s":""} · marked (inventory included)`} />
      <${Kpi} accent="gold" k="Estimated maker rebate"
        v=${money(agg.rebate)} sub=${`${agg.fills} fills · the real driver of std0's edge`} />
      <${Kpi} k="Marked P&L + rebate" v=${html`<span class=${signCls(agg.mark+agg.rebate)}>${moneyS(agg.mark+agg.rebate)}</span>`}
        sub="what std0 monetises at scale" />
    </div>` : null}

    <div class="howto warn">⚠ <b>Real money.</b> "Arm for REAL" sends REAL maker orders + an on-chain mint.
      The real path is still <b>experimental / quota-bound</b> until the self-submittable <b>Gnosis Safe</b> is
      deployed (<code>research/std0/NEXT-STEPS.md</code>): net-positive only at large scale (std0 blocks ~$500–2500).
      Start with <b>DRY-RUN</b> (no money, no key) to watch the machinery churn.</div>

    <${Panel} title=${html`Arm a runner — <span class="glyph" style="font-size:18px">⚒</span> ${cfgLabel(cfg)}`}
      aside=${html`<span class="mut">identity = mm-${cfg.underlying}-${cfg.interval} (one runner per identity)</span>`}>
      <div class="controls" style="margin-bottom:12px">
        <span class="field"><b>Asset</b> ${sel("underlying", coinOpts())}</span>
        <span class="field"><b>Frame</b> ${sel("interval", frameOpts())}</span>
        <span class="field"><b>Mint</b> ${num("mint_usd",5)} $ <span class="mut" title="size of the minted sets = ASK ammo (recovered on merge)">ammo</span></span>
        <span class="field"><b>Clip</b> ${num("clip",1)} shares <span class="mut" title="shares per resting order — CLOB minimum = 5">≥5</span></span>
        <span class="field"><b>Quote dist.</b> ${num("quote_dist",0.005)} <span class="mut" title="bid/ask distance to the mid — tighter = more fills = more rebate">tight</span></span>
        <span class="field"><b>Spot anchor</b> <input type="checkbox" checked=${cfg.spot_anchor} onChange=${e=>set("spot_anchor", e.target.checked)} /> <span class="mut" title="centres the quotes on the Binance fair-p (front-runs the CLOB lag, cuts pickoff)">anti-pickoff</span></span>
        <span class="field"><b>Poll</b> ${num("poll",0.5)} s <span class="mut" title="Dublin colocation ~24ms → sub-second cadence viable on the fast 5m">colo</span></span>
      </div>
      <div class="controls" style="margin-bottom:12px">
        <button class="btn sm ${adv?"primary":""}" onClick=${()=>setAdv(a=>!a)}>${adv?"▾":"▸"} Advanced settings</button>
      </div>
      ${adv ? html`<div class="controls" style="margin-bottom:16px">
        <span class="field"><b>β anchor</b> ${num("beta",0.1)} <span class="mut" title="0=pure CLOB mid, 1=pure spot fair-p">blend</span></span>
        <span class="field"><b>Touch back-off</b> ${num("back_off",0.005)} <span class="mut" title="sits behind the touch so it never crosses (post-only) and stays resting">rest</span></span>
        <span class="field"><b>Max inv</b> ${num("max_inv",1)} shares <span class="mut" title="max directional shares before skewing the quotes (defends neutrality)">skew</span></span>
        <span class="field"><b>Min quote</b> ${num("min_quote",0.05)}</span>
        <span class="field"><b>Max quote</b> ${num("max_quote",0.05)} <span class="mut" title="only operates while the mid sits in [min,max] (near 0.5 = two-sided)">band</span></span>
        <span class="field"><b>ε recenter</b> ${num("recenter_eps",0.005)} <span class="mut" title="re-posts once the mid drifts more than this">re-peg</span></span>
        <span class="field"><b>Flatten</b> ${num("flatten_buf",5)} s <span class="mut" title="stops quoting this long before settlement, then merges">before settl.</span></span>
        <span class="field"><b>Kill</b> ${num("kill_loss",1)} $ <span class="mut" title="cuts everything if the marked P&L drops below −kill">hard stop</span></span>
      </div>` : null}
      <${Note}>ℹ Research target = <b>BTC 5m</b> (64% of std0's volume/rebate) + <b>spot anchor</b> + <b>sub-second
        poll</b> (Dublin colocation). The merge recovers the minted block before settlement (more robust than redeem).</${Note}>
      <div class="controls" style="margin-top:14px">
        <button class="btn primary" onClick=${()=>act("start_dry")}>▶ Start DRY-RUN</button>
        <button class="btn danger" onClick=${()=>act("start_live")}>🔴 Arm for REAL</button>
      </div>
    </${Panel}>

    <${Panel} title="Rewards-MM runners" flush=${true}
      aside=${runners.length?html`<span class="mut">click a row for the detail · ⏸ pause · ▶ resume · ✕ remove</span>`:null}>
      <${DataTable} empty="No runner yet. Configure one above and start the DRY-RUN."
        rows=${runners} cols=${cols} onRowClick=${r=>setSelected(r.id)} />
    </${Panel}>

    ${sr ? html`<${MMDetail} sr=${sr} />` : null}`;
}

function MMDetail({sr}){
  const c = sr.status.config || {};
  const started = sr.status.started;
  const total = sr.mark + sr.rebate;
  return html`
    <${Panel} title=${html`Detail — <span class="glyph" style="font-size:18px">⚒</span> ${cfgLabel(c)}`}
      aside=${html`<span class="mut">${modeOf(sr)}${sr.alive&&started?` · since ${timeOf(started)}`:""}${sr.cur_slug?` · ${String(sr.cur_slug).replace(c.underlying+"-updown-","")}`:""}</span>`}>
      <div class="kpis">
        <${Kpi} real=${isLive(sr)} k="Marked P&L (inventory incl.)"
          v=${html`<span class=${signCls(sr.mark)}>${moneyS(sr.mark)}</span>`}
          sub=${`raw cash ${moneyS(sr.realized)} · mint $${fmt0(sr.minted)}`} />
        <${Kpi} accent="gold" k="Estimated maker rebate" v=${money(sr.rebate)}
          sub=${`${sr.fills} fills · marked+rebate ${moneyS(total)}`} />
        <${Kpi} k="Neutrality — inv Up/Down" num=${false}
          tone=${Math.abs(sr.neutrality)>sr.minted*0.5+1?"neg":""}
          v=${neutTxt(sr)} sub=${`Δ ${sr.neutrality} share(s) · neutral = net-zero redeem`} />
        <${Kpi} k="Resting quotes" v=${sr.resting} sub=${`spent ${money(sr.spent)} · received ${money(sr.received)}`} />
      </div>
    </${Panel}>

    <${Panel} title="Realised cash-flow (cumulative)"
      aside=${html`<span class="mut">≠ marked P&L — cash dips at the mint, comes back at merge/redeem</span>`}>
      ${(sr.curve||[]).length>=2
        ? html`<${LineChart} series=${[{pts:sr.curve, color:"#e8c264", name:"realised cash"}]} base=${0} single=${true} height=${220} />`
        : html`<${Empty}>Curve available from the 2nd cash movement (fill/merge).</${Empty}>`}
    </${Panel}>

    <${Panel} title="Runner journal" flush=${true}>
      <${DataTable} empty="No movement yet. Start the DRY-RUN to watch simulated quotes/fills."
        rows=${(sr.journal||[]).slice(-30).reverse()} cols=${journalCols} />
    </${Panel}>`;
}

const KIND_LABEL = {MINT:"⚒ mint", MERGE:"⚗ merge", FILL:"● fill", BID:"bid", ASK:"ask"};
const journalCols = [
  {key:"ts", label:"Time", cell:r=>html`<span class="num">${timeOf(+r.ts)}</span>`},
  {key:"kind", label:"Type", cell:r=>html`<span>${KIND_LABEL[r.kind]||r.kind}</span>`},
  {key:"order", label:"Order", cell:r=>html`<span class="mut">${r.order||"—"}</span>`},
  {key:"price", label:"Price", cell:r=>html`<span class="num">${r.price?(+r.price).toFixed(3):"—"}</span>`},
  {key:"shares", label:"Shares", cell:r=>html`<span class="num">${r.shares?fmt0(+r.shares):"—"}</span>`},
  {key:"inv", label:"Inv Up/Dn", cell:r=>html`<span class="num">${fmt0(+r.inv_up)}/${fmt0(+r.inv_dn)}</span>`},
  {key:"realized", label:"Cash", cell:r=>r.realized!==""&&r.realized!==undefined?html`<${Money} v=${+r.realized} signed=${true} />`:html`<span class="mut">—</span>`},
];
