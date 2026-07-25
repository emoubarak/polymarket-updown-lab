// Gains projection — TWO lenses on the same capital plan, side by side:
//   • Backtest   : the OOS simulation prior (ev_prior / tpd_prior).
//   • Live paper : the EV actually DISCOVERED live on the running paper run
//                  (ev_live / tpd_live), raw, no shrinkage.
// Capital controls (capital / stake / cap / regime / slippage) are shared.
import { html, useState } from '../preact.js';
import { pct, signCls, fmt0 } from '../format.js';
import { Kpi } from './ui.js';
import { LineChart } from './LineChart.js';

const H = [[1,"1d"],[7,"1w"],[30,"1mo"],[182,"6mo"],[365,"1y"]];
// the two projection sources — label, colour, and where to read EV/$ and bets/day
const SRC = [
  {key:"bt",   label:"Backtest",          color:"#a99c80", ev:m=>m.ev_prior||0, tpd:m=>m.tpd_prior||0},
  {key:"live", label:"Live (paper)", color:"#e8c264", ev:m=>m.ev_live||0,  tpd:m=>m.tpd_live||0},
];

const evAt = (ev,slip,clip,cap) => ev*Math.max(0, 1 - slip*Math.min(1, clip/cap));
function projValue(ev,tpd,base,frac,cap,reg,slip,days,compound){
  if(!compound){ const clip = Math.min(frac*base, cap);
    return Math.max(0, base + evAt(ev,slip,clip,cap)*clip*tpd*reg*days); }
  let B = base;
  for(let d=0; d<days; d++){ const clip = Math.min(frac*B, cap);
    B += evAt(ev,slip,clip,cap)*clip*tpd*reg; if(B<0.01) return 0; }
  return B;
}

export function Projection({m, stake}){
  const [p, setP] = useState({base:100, frac:10, cap:150, reg:100, slip:50});
  const set = (k,val) => setP(o => ({...o, [k]: +val}));
  const inp = {base:p.base, frac:p.frac/100, cap:p.cap, reg:p.reg/100, slip:p.slip/100};

  const n = m.n_settled||0, hasLive = n >= 1, hasBt = !!m.has_backtest, trip = m.win_rate > m.avg_price;
  const evBt = SRC[0].ev(m), evLive = SRC[1].ev(m);
  // a lens is plottable only if its source has data: backtest => a real OOS prior
  // (NOT the generic DEFAULT), live paper => at least one settled bet.
  const availOf = s => s.key === "live" ? hasLive : hasBt;

  const field = (lab, key, suffix) => html`<span class="field"><b>${lab}</b>
    <input class="ctl" type="number" value=${p[key]} onInput=${e=>set(key, e.target.value)} />${suffix||""}</span>`;

  // one 6-month compound trajectory per AVAILABLE source (the headline curve)
  const series = SRC.filter(availOf).map(s => {
    const ev = s.ev(m), tpd = s.tpd(m), pts=[];
    for(let d=0; d<=182; d++) pts.push([d, projValue(ev,tpd,inp.base,inp.frac,inp.cap,inp.reg,inp.slip,d,true)]);
    return {pts, color:s.color, name:`${s.label} (compounded)`};
  });

  // table: each source × (flat stake, compounded). A row shows "—" until its source has data.
  const cell = (ev,tpd,d,compound,cls,avail) => {
    if(!avail) return html`<td class="mut">—</td>`;
    const val = projValue(ev,tpd,inp.base,inp.frac,inp.cap,inp.reg,inp.slip,d,compound), g = val-inp.base;
    return html`<td class=${cls}>$${fmt0(val)}<div class=${"cell-sub "+signCls(g)}>${g>=0?"+":""}${fmt0(g)}</div></td>`;
  };
  const rows = SRC.flatMap(s => { const avail = availOf(s); return [
    {s, avail, label:`${s.label} — flat stake`, comp:false, cls:"lin"},
    {s, avail, label:`${s.label} — compounded`, comp:true,  cls:"cmp"},
  ]; });

  return html`
    <div class="controls" style="padding-top:14px;margin-bottom:16px">
      ${field("capital","base"," $")} ${field("stake","frac"," % / bet")} ${field("cap","cap"," $")}
      <span class="field"><b>regime</b><input class="ctl" type="number" value=${p.reg} onInput=${e=>set("reg",e.target.value)} /> %
        <button class="btn" onClick=${()=>set("reg",100)}>nominal</button>
        <button class="btn" onClick=${()=>set("reg",55)}>honest</button></span>
      ${field("slippage","slip"," %")}
    </div>

    <div class="kpis">
      <${Kpi} k="edge / $ — 📊 backtest" v=${hasBt?html`<span class=${signCls(evBt)}>${(evBt>=0?"+":"")+evBt.toFixed(4)}</span>`:"—"}
        sub=${hasBt?"past data (OOS)":"no clean backtest"} />
      <${Kpi} k="edge / $ — 🟡 live" v=${html`<span class=${signCls(evLive)}>${hasLive?(evLive>=0?"+":"")+evLive.toFixed(4):"—"}</span>`}
        sub=${`discovered live · n=${n}`} />
      <${Kpi} k="bets / day" v=${SRC[0].tpd(m).toFixed(1)} sub=${`observed ${SRC[1].tpd(m).toFixed(1)}`} />
      <${Kpi} k="win rate vs price" num=${false}
        v=${hasLive?`${pct(m.win_rate)} / ${pct(m.avg_price)}`:"—"}
        sub=${hasLive?(trip?"live edge":"edge ≤ price"):"n/a"} />
    </div>

    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>projection</th>${H.map(h=>html`<th>${h[1]}</th>`)}</tr></thead>
      <tbody>${rows.map(r => html`<tr><td><span style=${`color:${r.s.color}`}>●</span> ${r.label}</td>${
        H.map(([d]) => cell(r.s.ev(m), r.s.tpd(m), d, r.comp, r.cls, r.avail))
      }</tr>`)}</tbody></table></div>

    <h2 class="proj-h">6-month compounded trajectory — <span style="color:#a99c80">📊 backtest (past)</span> vs <span class="gold">🟡 live (paper)</span></h2>
    ${series.length
      ? html`<${LineChart} series=${series} base=${inp.base} height=${240} />`
      : html`<div class="note-box">Nothing to plot yet: no clean backtest, and no settled bet for this strategy.</div>`}
    <div class="note-box" style="margin-top:14px">Two lenses, same capital settings:
      <b>📊 backtest</b> = the edge/$ measured on <b>past data</b> (history, OOS);
      <b>🟡 live</b> = the edge/$ actually <b>realized</b> by the paper run going
      <b>right now</b>, raw. A strategy without a clean backtest shows "—" on the backtest
      side rather than a misleading generic number. Below ~150 settled bets the live figure is
      noisy — provisional (n=${n}).
      <b>Slippage</b>: the edge erodes as the stake grows (down to −${p.slip} % at the cap).
      Long columns assume the edge holds: a ceiling, not a promise.</div>`;
}
