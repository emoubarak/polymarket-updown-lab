// Reusable sortable table. Click a sortable header to sort (toggles asc/desc).
//
//   cols: [{ key, label, sortable?, title?,
//            get?(row)  -> raw value (sorting + default cell),
//            cell?(row) -> custom render (htm),
//            tdCls?(row)-> extra <td> class }]
//   rows: array of objects   ·   sort: {key, dir:-1|1}   ·   onRowClick?(row)
import { html, useState, useMemo } from '../preact.js';

export function DataTable({cols, rows, sort, onRowClick, empty}){
  const [s, setS] = useState(sort || null);

  const sorted = useMemo(() => {
    if(!s) return rows;
    const col = cols.find(c => c.key === s.key);
    if(!col) return rows;
    const get = col.get || (r => r[s.key]);
    return rows.slice().sort((a, b) => {
      let va = get(a), vb = get(b);
      if(typeof va === "string" || typeof vb === "string")
        return s.dir * String(va ?? "").localeCompare(String(vb ?? ""));
      return s.dir * (((va ?? 0)) - ((vb ?? 0)));
    });
  }, [rows, s, cols]);

  const onSort = c => { if(!c.sortable) return;
    setS(p => (p && p.key === c.key) ? {key:c.key, dir:-p.dir} : {key:c.key, dir:-1}); };

  if(!rows.length) return html`<div class="empty">${empty || "Aucune donnée."}</div>`;

  // sortable headers are keyboard-operable (Tab to focus, Enter/Space to sort) and
  // announce direction via aria-sort, so the table isn't mouse-only.
  const ariaSort = c => (s && s.key === c.key) ? (s.dir < 0 ? "descending" : "ascending") : (c.sortable ? "none" : null);
  return html`<div class="tbl-wrap"><table class="tbl">
    <thead><tr>${cols.map(c => html`
      <th scope="col" class=${c.sortable ? "sortable" : ""} title=${c.title || ""}
          aria-sort=${ariaSort(c)} tabindex=${c.sortable ? "0" : null} role=${c.sortable ? "button" : null}
          onClick=${() => onSort(c)}
          onKeyDown=${c.sortable ? (e => { if(e.key === "Enter" || e.key === " "){ e.preventDefault(); onSort(c); } }) : null}>
        ${c.label}${s && s.key === c.key ? html`<span class="ar">${s.dir < 0 ? "▾" : "▴"}</span>` : null}</th>`)}
    </tr></thead>
    <tbody>${sorted.map(r => html`
      <tr class=${onRowClick ? "clickable" : ""} onClick=${onRowClick ? () => onRowClick(r) : null}>
        ${cols.map(c => html`<td class=${c.tdCls ? c.tdCls(r) : ""}>
          ${c.cell ? c.cell(r) : (c.get ? c.get(r) : r[c.key])}</td>`)}
      </tr>`)}</tbody>
  </table></div>`;
}
