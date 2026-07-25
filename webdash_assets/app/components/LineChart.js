// Equity-curve chart — a Preact wrapper over Chart.js (vendored UMD global).
// Created once on mount, then updated in place each poll (no flicker, no leak).
//
//   series: [{ name, color, pts:[[ts,value],...], hidden? }]   ·   base = starting capital
import { html, useRef, useEffect } from '../preact.js';

const AXIS = "#a99c80", GRID = "rgba(255,255,255,.05)", FONT = {family:"Plex Mono, monospace", size:10};
const dt = ts => new Date(ts*1000).toLocaleString("en-GB",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});

function datasets(series, base){
  const ds = series.map(s => ({
    label: s.name || "", data: s.pts.map(p => ({x:p[0], y:p[1]})),
    borderColor: s.color, backgroundColor: s.color,
    borderWidth: 2, pointRadius: 0, pointHitRadius: 6, tension: .15, hidden: !!s.hidden,
  }));
  if(base != null){
    const xs = series.flatMap(s => s.pts.map(p => p[0]));
    if(xs.length) ds.push({ label: "start", data: [{x:Math.min(...xs), y:base}, {x:Math.max(...xs), y:base}],
      // --ink-3 (#948668): the current WCAG-passing muted ink (the old #776c55 --ink-3 was
      // retired for failing AA); one notch dimmer than the AXIS, fitting a baseline reference.
      borderColor: "#948668", borderDash: [4,5], borderWidth: 1, pointRadius: 0, order: 99 });
  }
  return ds;
}

export function LineChart({series, base, height = 270, single = false, xfmt}){
  const ref = useRef(null), chart = useRef(null);
  const fmtX = xfmt || dt;   // default x is a unix-ts date; pass xfmt for e.g. a trade index

  useEffect(() => {
    // No canvas in the DOM when there aren't enough points (the `enough` guard
    // below returns an empty div first) → ref.current is null. Bail rather than
    // crash on null.getContext — this is the fresh/after-reset state (no equity yet).
    if(!ref.current){ return; }
    const ds = datasets(series, base);
    if(chart.current){ chart.current.data.datasets = ds; chart.current.update("none"); return; }
    chart.current = new window.Chart(ref.current.getContext("2d"), {
      type: "line", data: {datasets: ds},
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: {mode:"index", intersect:false},
        scales: {
          x: {type:"linear", grid:{color:GRID}, ticks:{color:AXIS, font:FONT, maxTicksLimit:7, callback:fmtX}},
          y: {grid:{color:GRID}, ticks:{color:AXIS, font:FONT, callback:v => "$"+Math.round(v)}},
        },
        plugins: {
          legend: single ? {display:false} : {position:"top", align:"end",
            labels: {color:AXIS, font:FONT, boxWidth:14, boxHeight:3, usePointStyle:false,
              filter: it => it.text !== "start"}},
          tooltip: {backgroundColor:"#15120b", borderColor:"#4f4330", borderWidth:1,
            titleColor:"#ece2cf", bodyColor:"#ece2cf", titleFont:FONT, bodyFont:FONT, padding:9,
            callbacks: {title: it => fmtX(it[0].parsed.x),
              label: c => (c.dataset.label && c.dataset.label!=="start" ? " "+c.dataset.label+": " : " ")
                + "$" + c.parsed.y.toFixed(2)}},
        },
      },
    });
  });

  useEffect(() => () => { if(chart.current){ chart.current.destroy(); chart.current = null; } }, []);

  const enough = series.some(s => s.pts && s.pts.length >= 2);
  if(!enough) return html`<div class="empty">Not enough points yet to draw the curve.</div>`;
  return html`<div class="chart-box" style=${"height:" + height + "px"}><canvas ref=${ref}></canvas></div>`;
}
