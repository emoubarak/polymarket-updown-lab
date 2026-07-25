// Left rail — scales to N strategies (the old 13-tab horizontal bar didn't).
// Real money sits at the top with its live P&L inline; each strategy shows a
// verdict dot + paper P&L so the whole race reads at a glance.
//
// The race itself is no longer one flat chain: a grouping selector (Strat / Coin /
// Frame) folds the runners into collapsible sub-groups along the chosen axis, each
// header carrying the group's aggregate P&L + count. Scales as the breadth grows.
// Groups start COLLAPSED (a 48-runner rail is unusable open) — click a header to
// expand it. The header of the group holding the current page is accented so you
// stay oriented without expanding.
import { html, useState } from '../preact.js';
import { CONFIG, useNow } from '../api.js';
import { glyphOf, nameOf, partsOf, verdict, moneyS, signCls, ageStr } from '../format.js';

const VDOT = {proven:"var(--verdigris)", none:"var(--vermilion)", trial:"var(--amber)", wait:"var(--ink-3)"};
const VLABEL = {proven:"Proven edge", none:"No edge", trial:"On trial", wait:"Waiting"};

// The three sub-nav axes — `key` indexes partsOf(), `lab` is the button caption.
const AXES = [{key:"brain", lab:"Strat"}, {key:"token", lab:"Coin"}, {key:"frame", lab:"Frame"}];
const FRAME_ORDER = {"5m":0, "15m":1};
// group label: coins uppercased (BTC), frames as-is (15m), brains as-is (zlead).
const groupLabel = (axis, v) => axis==="token" ? v.toUpperCase() : v;
// Persisted across remounts: chosen axis + the set of EXPANDED group keys
// (empty = all collapsed, the default; namespaced by axis so the expand state
// doesn't bleed when you switch axis).
const NAV = {by:"token", expanded:new Set()};

// Live connection/freshness badge — replaces the old decorative always-green dot,
// which lied when the server was down. green = fresh · amber = stale · red = reconnecting.
function ConnStatus({conn}){
  const now = useNow(1000);
  if(!conn || (!conn.updatedAt && conn.loading))
    return html`<div class="live"><span class="dot" style="background:var(--ink-3)"></span>connecting…</div>`;
  const ageS = conn.updatedAt ? (now - conn.updatedAt)/1000 : null;
  const reconnecting = !!conn.error;
  const stale = ageS != null && ageS > 25;
  const c = reconnecting ? "var(--vermilion)" : stale ? "var(--amber)" : "var(--verdigris)";
  const txt = reconnecting ? "reconnecting…" : `live · upd ${ageStr(ageS)}`;
  return html`<div class="live" title=${conn.error || "live feed (auto 10 s)"}>
    <span class="dot" style=${"background:"+c+(reconnecting?"":";box-shadow:0 0 8px "+c)}></span>${txt}</div>`;
}

function Item({active, real, glyph, label, right, onClick}){
  return html`<button class=${"nav-item" + (active?" active":"") + (real?" real":"")}
      onClick=${onClick} aria-current=${active?"page":null}>
    <span class="g" aria-hidden="true">${glyph}</span><span class="lab">${label}</span>${right || null}</button>`;
}

// One runner row in the race list (verdict dot + paper P&L on the right).
function StratItem({n, route, all, go}){
  const d = (all && all[n]) || {}, s = d.state || {}, m = d.metrics || {};
  const v = verdict(m), live = s.realized_pnl || 0;
  return html`<${Item} active=${route==="strat/"+n} glyph=${glyphOf(n)} label=${nameOf(n)}
    onClick=${()=>go("strat/"+n)}
    right=${html`<span class="nav-dot" style=${"background:"+VDOT[v.key]} title=${v.txt}
        role="img" aria-label=${VLABEL[v.key]}></span>
      <span class=${"pnl "+signCls(live)}>${all?moneyS(live):"·"}</span>`} />`;
}

// Fold CONFIG.STRATS into ordered groups along `axis` (first-appearance order, but
// frames forced 5m→15m). No aggregate P&L per group: a group mixes heterogeneous
// runners (BTC group = different brains; zlead group = different coins), so a summed
// P&L would be meaningless — only the per-runner number (in StratItem) is.
function groupStrats(axis){
  const order = [], byKey = {};
  for(const n of CONFIG.STRATS){
    const k = partsOf(n)[axis] || "—";
    if(!byKey[k]){ byKey[k] = []; order.push(k); }
    byKey[k].push(n);
  }
  if(axis==="frame") order.sort((a,b)=>(FRAME_ORDER[a]??9)-(FRAME_ORDER[b]??9));
  return order.map(k => ({key:k, items:byKey[k]}));
}

export function Sidebar({route, all, conn, pilot, mm, go}){
  // wallet = the ONE shared real wallet (see collect_pilots). Real-time P&L = current
  // mark-to-market capital − deposit baseline; moves with open positions. (The old
  // pilot.realized_pnl was a single-pilot leftover — /pilot-data is a list now → it read 0.)
  const w = (pilot && pilot.wallet) || {};
  const plPnl = w.pnl || 0;
  const plLive = !!w.live;
  // rewards-MM badge = marked P&L + estimated rebate across live runners (its true take).
  const mAgg = (mm && mm.agg) || {};
  const mmPnl = (mAgg.mark || 0) + (mAgg.rebate || 0);
  const mmLive = (mAgg.n_real || 0) > 0;

  // grouping axis + collapse state, mirrored to the module so they survive a remount
  const [by, setByS] = useState(NAV.by);
  const [, bump] = useState(0);
  const setBy = v => { NAV.by = v; setByS(v); };
  const ck = k => by + ":" + k;                       // axis-namespaced expand key
  const toggle = k => { const e = NAV.expanded;
    e.has(ck(k)) ? e.delete(ck(k)) : e.add(ck(k)); bump(x => x + 1); };
  const groups = groupStrats(by);
  const activeName = route.startsWith("strat/") ? route.slice(6) : null;

  return html`<aside class="sidebar">
    <div class="brand">
      <span class="sigil" aria-hidden="true">🜍</span>
      <div><span class="wm"><b>poly</b>updown</span>
        <span class="tag">the lab</span></div>
    </div>
    <${ConnStatus} conn=${conn} />

    <${Item} active=${route==="overview"} glyph="◈" label="Overview" onClick=${()=>go("overview")} />
    <${Item} active=${route==="correlation"} glyph="▦" label="Correlations" onClick=${()=>go("correlation")} />
    <${Item} active=${route==="pilot"} real=${true} glyph="⚗" label="The pilot ${plLive?"🔴":""}"
      onClick=${()=>go("pilot")}
      right=${html`<span class=${"pnl "+signCls(plPnl)}>${moneyS(plPnl)}</span>`} />
    <${Item} active=${route==="mm"} real=${mmLive} glyph="⚒" label="Rewards-MM ${mmLive?"🔴":""}"
      onClick=${()=>go("mm")}
      right=${mmLive?html`<span class=${"pnl "+signCls(mmPnl)}>${moneyS(mmPnl)}</span>`:null} />
    <${Item} active=${route==="events"} glyph="🗳" label="Events" onClick=${()=>go("events")} />
    <${Item} active=${route==="copy"} glyph="🪞" label="Wallet copy" onClick=${()=>go("copy")} />
    <${Item} active=${route==="history"} glyph="🕰" label="History" onClick=${()=>go("history")} />

    <div class="nav-group">Strategies — the race</div>
    <div class="nav-tools" role="group" aria-label="Group strategies by">
      ${AXES.map(a => html`<button class=${"nav-tool"+(by===a.key?" on":"")}
        onClick=${()=>setBy(a.key)} aria-pressed=${by===a.key}>${a.lab}</button>`)}
    </div>
    ${groups.map(g => {
      const col = !NAV.expanded.has(ck(g.key));
      const hasActive = activeName && g.items.includes(activeName);
      return html`
        <button class=${"nav-sub"+(col?" col":"")+(hasActive?" has-active":"")}
            onClick=${()=>toggle(g.key)} aria-expanded=${!col}>
          <span class="chev" aria-hidden="true">${col?"▸":"▾"}</span>
          <span class="lab">${groupLabel(by, g.key)}</span>
          <span class="cnt">${g.items.length}</span>
        </button>
        <div class=${"nav-grp-items"+(col?" collapsed":"")}>
          ${g.items.map(n => html`<${StratItem} n=${n} route=${route} all=${all} go=${go} />`)}
        </div>`;
    })}

    <${Item} active=${route==="about"} glyph="☉" label="The method" onClick=${()=>go("about")} />
    <div class="nav-foot">paper money · stdlib + preact</div>
  </aside>`;
}
