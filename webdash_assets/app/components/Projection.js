// Gains projection — TWO lenses on the same capital plan, side by side:
//   • Backtest   : the OOS simulation prior (ev_prior / tpd_prior).
//   • Paper réel : the EV actually DISCOVERED live on the running paper run
//                  (ev_live / tpd_live), raw, no shrinkage.
// Capital controls (capital / mise / plafond / régime / slippage) are shared.
import { html, useState } from '../preact.js';
import { pct, signCls, fmt0 } from '../format.js';
import { Kpi } from './ui.js';
import { LineChart } from './LineChart.js';

const H = [[1,"1j"],[7,"1sem"],[30,"1mois"],[182,"6mois"],[365,"1an"]];
// the two projection sources — label, colour, and where to read EV/$ and bets/day
const SRC = [
  {key:"bt",   label:"Backtest",          color:"#a99c80", ev:m=>m.ev_prior||0, tpd:m=>m.tpd_prior||0},
  {key:"live", label:"En direct (papier)", color:"#e8c264", ev:m=>m.ev_live||0,  tpd:m=>m.tpd_live||0},
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
  // (NOT the generic DEFAULT), paper réel => at least one settled bet.
  const availOf = s => s.key === "live" ? hasLive : hasBt;

  const field = (lab, key, suffix) => html`<span class="field"><b>${lab}</b>
    <input class="ctl" type="number" value=${p[key]} onInput=${e=>set(key, e.target.value)} />${suffix||""}</span>`;

  // one 6-month compound trajectory per AVAILABLE source (the headline curve)
  const series = SRC.filter(availOf).map(s => {
    const ev = s.ev(m), tpd = s.tpd(m), pts=[];
    for(let d=0; d<=182; d++) pts.push([d, projValue(ev,tpd,inp.base,inp.frac,inp.cap,inp.reg,inp.slip,d,true)]);
    return {pts, color:s.color, name:`${s.label} (réinvesti)`};
  });

  // table: each source × (mise fixe, réinvesti). A row shows "—" until its source has data.
  const cell = (ev,tpd,d,compound,cls,avail) => {
    if(!avail) return html`<td class="mut">—</td>`;
    const val = projValue(ev,tpd,inp.base,inp.frac,inp.cap,inp.reg,inp.slip,d,compound), g = val-inp.base;
    return html`<td class=${cls}>$${fmt0(val)}<div class=${"cell-sub "+signCls(g)}>${g>=0?"+":""}${fmt0(g)}</div></td>`;
  };
  const rows = SRC.flatMap(s => { const avail = availOf(s); return [
    {s, avail, label:`${s.label} — mise fixe`, comp:false, cls:"lin"},
    {s, avail, label:`${s.label} — réinvesti`, comp:true,  cls:"cmp"},
  ]; });

  return html`
    <div class="controls" style="padding-top:14px;margin-bottom:16px">
      ${field("capital","base"," $")} ${field("mise","frac"," % / pari")} ${field("plafond","cap"," $")}
      <span class="field"><b>régime</b><input class="ctl" type="number" value=${p.reg} onInput=${e=>set("reg",e.target.value)} /> %
        <button class="btn" onClick=${()=>set("reg",100)}>nominal</button>
        <button class="btn" onClick=${()=>set("reg",55)}>honnête</button></span>
      ${field("slippage","slip"," %")}
    </div>

    <div class="kpis">
      <${Kpi} k="avantage / $ — 📊 backtest" v=${hasBt?html`<span class=${signCls(evBt)}>${(evBt>=0?"+":"")+evBt.toFixed(4)}</span>`:"—"}
        sub=${hasBt?"données passées (OOS)":"pas de backtest propre"} />
      <${Kpi} k="avantage / $ — 🟡 en direct" v=${html`<span class=${signCls(evLive)}>${hasLive?(evLive>=0?"+":"")+evLive.toFixed(4):"—"}</span>`}
        sub=${`découvert en direct · n=${n}`} />
      <${Kpi} k="paris / jour" v=${SRC[0].tpd(m).toFixed(1)} sub=${`observé ${SRC[1].tpd(m).toFixed(1)}`} />
      <${Kpi} k="réussite vs prix" num=${false}
        v=${hasLive?`${pct(m.win_rate)} / ${pct(m.avg_price)}`:"—"}
        sub=${hasLive?(trip?"avantage vivant":"avantage ≤ prix"):"n/a"} />
    </div>

    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>projection</th>${H.map(h=>html`<th>${h[1]}</th>`)}</tr></thead>
      <tbody>${rows.map(r => html`<tr><td><span style=${`color:${r.s.color}`}>●</span> ${r.label}</td>${
        H.map(([d]) => cell(r.s.ev(m), r.s.tpd(m), d, r.comp, r.cls, r.avail))
      }</tr>`)}</tbody></table></div>

    <h2 class="proj-h">Trajectoire 6 mois réinvestie — <span style="color:#a99c80">📊 backtest (passé)</span> vs <span class="gold">🟡 en direct (papier)</span></h2>
    ${series.length
      ? html`<${LineChart} series=${series} base=${inp.base} height=${240} />`
      : html`<div class="note-box">Rien à tracer encore : ni backtest propre, ni pari réglé pour cette stratégie.</div>`}
    <div class="note-box" style="margin-top:14px">Deux lentilles, mêmes réglages de capital :
      <b>📊 backtest</b> = l'avantage/$ mesuré sur les <b>données passées</b> (historique, OOS) ;
      <b>🟡 en direct</b> = l'avantage/$ réellement <b>réalisé</b> par le run papier qui tourne
      <b>maintenant</b>, brut. Une stratégie sans backtest propre affiche « — » côté backtest
      plutôt qu'un chiffre générique trompeur. Sous ~150 paris réglés le chiffre en direct est
      bruité — provisoire (n=${n}).
      <b>Slippage</b> : l'edge s'érode quand la mise grossit (jusqu'à −${p.slip} % au plafond).
      Colonnes longues = avantage supposé intact : un plafond, pas une promesse.</div>`;
}
