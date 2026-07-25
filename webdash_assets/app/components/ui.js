// UI primitives — the uniform vocabulary every page composes from.
import { html } from '../preact.js';
import { money, moneyS, signCls, pct } from '../format.js';

// instrument card: label · big value · sub. accent ∈ gold|verd|merc; real = red frame.
export const Kpi = ({k, v, sub, accent, real, glyph, tone, num = true}) => html`
  <div class=${"kpi " + (real ? "real " : "") + (accent || "")}>
    ${glyph ? html`<div class="tagline">${glyph}</div>` : null}
    <div class="k">${k}</div>
    <div class=${"v " + (num ? "num " : "") + (tone || "")}>${v}</div>
    ${sub != null ? html`<div class="s">${sub}</div>` : null}
  </div>`;

export const Panel = ({title, aside, flush, children}) => html`
  <section class="panel">
    ${(title || aside) ? html`<div class="panel-head">
        <h2>${title || ""}</h2>${aside != null ? html`<span class="aside">${aside}</span>` : null}</div>` : null}
    <div class=${"panel-body" + (flush ? " flush" : "")}>${children}</div>
  </section>`;

// THE signature — the assay seal: has base metal become gold? metal-coded verdict.
const SEAL_MARK = {proven:"✦", trial:"◐", none:"▽", wait:"·"};
export const Seal = ({v, glyph, lg}) => html`
  <span class=${"seal " + v.key + (lg ? " lg" : "")} title=${v.help} role="img" aria-label=${v.txt + " — " + v.help}>
    <span class="mk" aria-hidden="true">${glyph || SEAL_MARK[v.key] || "·"}</span>${v.txt}</span>`;

// the magnum-opus gauge: progress lead → gold (n/150 + win beats price)
export const Opus = ({m}) => {
  const n = m.n_settled || 0, prog = Math.min(1, n / 150) * 100;
  const above = n && m.win_rate > m.avg_price;
  const fill = above ? "linear-gradient(90deg,#5a5040,var(--gold))"
                     : "linear-gradient(90deg,#4a3128,var(--vermilion))";
  return html`<div class="opus">
    <div class="opus-top">
      <span class="t">The work — toward a proven edge</span>
      <span class=${"num " + (above ? "gold" : n ? "neg" : "mut")}>
        ${n ? pct(m.win_rate) + " vs price " + pct(m.avg_price) : "—"}</span></div>
    <div class="opus-track"><div class="opus-fill" style=${"width:" + prog + "%;background:" + fill}></div>
      <div class="opus-mark" style="left:100%"></div></div>
    <div class="opus-legend"><span>${"lead · " + n + "/150 bets"}</span><span>${"gold · 150 bets + win > price"}</span></div>
  </div>`;
};

export const Lamp = ({lamp}) => html`
  <div class="lamp-row"><span class="lamp" style=${"color:" + lamp.c + ";background:" + lamp.c}
    role="img" aria-label=${lamp.t}></span>${lamp.t}</div>`;

export const HealthDot = ({h}) => html`<span class="dot" style=${"background:" + h.c}
  role="img" aria-label=${"engine " + h.t}></span>${h.t}`;

export const Tag = ({tone, title, children}) =>
  html`<span class=${"tag " + (tone || "")} title=${title || ""}>${children}</span>`;

export const Stat = ({k, v, tone}) =>
  html`<div class="stat"><span class="k">${k}</span><span class=${"v " + (tone || "")}>${v}</span></div>`;

export const Note = ({tone, children}) => html`<div class=${"note-box " + (tone || "")}>${children}</div>`;
export const Empty = ({children}) => html`<div class="empty">${children}</div>`;

// colored money number, used inside table cells
export const Money = ({v, signed}) =>
  html`<span class=${"num " + signCls(v)}>${signed ? moneyS(v) : money(v)}</span>`;
