// Entry point: load config, then render the app shell (sidebar + routed page).
// Hash routing: #/overview · #/correlation · #/pilot · #/events · #/copy · #/history · #/about · #/strat/<name>.
import { html, render, useState, useEffect, Component } from './preact.js';
import { loadConfig, usePoll } from './api.js';
import { Sidebar } from './components/Sidebar.js';
import { Overview } from './pages/Overview.js';
import { Correlation } from './pages/Correlation.js';
import { Strategy } from './pages/Strategy.js';
import { Pilot } from './pages/Pilot.js';
import { MM } from './pages/MM.js';
import { Events } from './pages/Events.js';
import { CopyMirror } from './pages/CopyMirror.js';
import { History } from './pages/History.js';
import { About } from './pages/About.js';

const parseHash = () => (location.hash.replace(/^#\/?/, "") || "overview");

const TITLES = {overview:"Overview", correlation:"Correlations", pilot:"The pilot",
  mm:"Rewards-MM", events:"Events", copy:"Wallet copy", history:"History", about:"The method"};
const titleFor = r => "pmlab — "
  + (r.startsWith("strat/") ? r.slice(6) : (TITLES[r] || "the lab"));

function useRoute(){
  const [r, setR] = useState(parseHash());
  useEffect(() => {
    const f = () => setR(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  // each route change: scroll to top + retitle the tab (deep-link friendly)
  useEffect(() => { window.scrollTo(0, 0); document.title = titleFor(r); }, [r]);
  return r;
}

// A render error in one page must not white-screen the whole instrument panel.
class Boundary extends Component {
  constructor(p){ super(p); this.state = {err:null}; }
  static getDerivedStateFromError(err){ return {err}; }
  componentDidUpdate(prev){ if(prev.route !== this.props.route && this.state.err) this.setState({err:null}); }
  render(){
    if(this.state.err) return html`<div class="empty" style="padding:48px">
      This page crashed: ${String(this.state.err)}.
      <div style="margin-top:10px"><button class="btn" onClick=${()=>this.setState({err:null})}>retry</button></div></div>`;
    return this.props.children;
  }
}

function Page({route, all, pilot, go}){
  if(route === "overview") return html`<${Overview} all=${all} pilot=${pilot} go=${go} />`;
  if(route === "correlation") return html`<${Correlation} />`;
  if(route === "pilot")    return html`<${Pilot} all=${all} />`;
  if(route === "mm")       return html`<${MM} />`;
  if(route === "events")   return html`<${Events} />`;
  if(route === "copy")     return html`<${CopyMirror} />`;
  if(route === "history")  return html`<${History} />`;
  if(route === "about")    return html`<${About} />`;
  if(route.startsWith("strat/")) return html`<${Strategy} name=${route.slice(6)} />`;
  return html`<div class="empty">Unknown page.</div>`;
}

function App(){
  const route = useRoute();
  // /all drives the sidebar P&L + verdict dots AND the overview — fetched once here.
  // Keep the whole poll result (data + error + updatedAt) so the sidebar can show
  // a live connection/freshness badge instead of a decorative "always green" dot.
  const allP = usePoll("/all");
  const pilot = usePoll("/pilot-data").data;
  const mm = usePoll("/mm-data").data;
  const go = r => { location.hash = "#/" + r; };
  return html`<div class="app">
    <${Sidebar} route=${route} all=${allP.data} conn=${allP} pilot=${pilot} mm=${mm} go=${go} />
    <main class="main"><${Boundary} route=${route}>
      <${Page} route=${route} all=${allP.data} pilot=${pilot} go=${go} />
    </${Boundary}></main>
  </div>`;
}

loadConfig()
  .then(() => render(html`<${App} />`, document.getElementById("root")))
  .catch(e => {
    document.getElementById("root").innerHTML =
      `<div class="empty">Failed to load config: ${e}. Is the dashboard up to date?</div>`;
  });
