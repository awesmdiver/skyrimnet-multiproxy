"""The dashboard's "Help" tab (2026-10-01), built to design/dashboard-help-mockup.html. The pages themselves come from
docs/help/help-content.md (see help_content.py); this is only the frame: topic list with a search box, live dots, buttons.
Plain strings in their own file, spliced into multiproxy.py's page, so none of the CSS/JS needs doubled braces.
"""
import tab_desc_ui

HELP_TAB_HTML = """<div class="tab" id="tabHelp" onclick="showMainTab('help')">Help</div>"""

HELP_HOW_LINK = """<span class="hp-how"><a onclick="openHelp('%s')">How do I set this up?</a></span>"""

HELP_CSS = r"""
  /* Help tab (2026-10-01) -- hp-* classes */
  .hp { display:grid; grid-template-columns:230px 1fr; gap:22px; align-items:start }
  .hp-nav { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; position:sticky; top:16px; max-height:calc(100vh - 40px); overflow-y:auto }
  .hp-nav input { width:100%; box-sizing:border-box; margin-bottom:10px; font-size:0.82rem }
  .hp-grp { font-size:0.68rem; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted); margin:14px 0 4px }
  .hp-grp:first-of-type { margin-top:4px }
  .hp-nav a { display:flex; align-items:center; gap:8px; padding:5px 8px; border-radius:6px; font-size:0.85rem; color:var(--text); text-decoration:none; cursor:pointer }
  .hp-nav a:hover { background:var(--panel2, var(--line)) }
  .hp-nav a.on { background:color-mix(in srgb, var(--accent) 14%, var(--panel)); color:var(--text) }
  .hp-dot { width:8px; height:8px; border-radius:50%; background:var(--muted); flex-shrink:0; display:inline-block }
  .hp-dot.ok { background:var(--ok) } .hp-dot.warn { background:var(--warn) }
  .hp-page { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:24px 28px; min-width:0 }
  .hp-page h2 { font-size:1.2rem; margin:0 0 6px }
  .hp-page h3 { font-size:0.9rem; margin:18px 0 4px; color:var(--accent-text) }
  .hp-page p, .hp-page li { font-size:0.9rem; line-height:1.55 }
  .hp-page ol, .hp-page ul { padding-left:20px; margin:6px 0 }
  .hp-page li { margin:5px 0 }
  .hp-page code { font-family:ui-monospace,Consolas,monospace; font-size:0.82rem; color:var(--accent-text); background:var(--bg); border:1px solid var(--line); border-radius:4px; padding:1px 6px }
  .hp-page a { color:var(--accent-text) }
  .hp-lead { color:var(--muted); font-size:0.95rem !important; margin:0 0 6px }
  .hp-note { color:var(--muted); font-weight:normal }
  .hp-groupnote { color:var(--muted); font-size:0.82rem !important; border-left:2px solid var(--line); padding-left:10px; margin:0 0 12px }
  .hp-status { display:inline-flex; align-items:center; gap:8px; font-size:0.8rem; color:var(--muted); border:1px solid var(--line); border-radius:8px; padding:3px 10px; margin-bottom:8px }
  .hp-cmd { display:flex; gap:8px; align-items:center; background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin:6px 0; font-family:ui-monospace,Consolas,monospace; font-size:0.82rem; color:var(--accent-text) }
  .hp-cmd > span { flex:1; overflow-x:auto; white-space:nowrap }
  .hp-mini { font-size:0.68rem !important; padding:1px 7px !important; margin-left:2px }
  .hp-callout { border-left:3px solid var(--warn); background:color-mix(in srgb, var(--warn) 10%, var(--panel)); border-radius:6px; padding:9px 12px; font-size:0.85rem; margin:10px 0 }
  .hp-go { display:flex; gap:8px; flex-wrap:wrap; margin-top:18px; padding-top:14px; border-top:1px solid var(--line) }
  .hp-page table { border-collapse:collapse; width:100%; font-size:0.85rem; margin:8px 0 }
  .hp-page td, .hp-page th { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top }
  .hp-page th { color:var(--muted); font-weight:500; font-size:0.78rem }
  .hp-page h4 { font-size:0.74rem; letter-spacing:0.06em; text-transform:uppercase; color:var(--muted); margin:18px 0 4px }
  .hp-page td { overflow-wrap:break-word; font-size:0.82rem }
  .hp-page td:first-child { white-space:nowrap }
  .hp-link { color:var(--accent-text); cursor:pointer; text-decoration:underline }
  .hp-gl dt { font-weight:600; font-size:0.9rem; margin:14px 0 2px; scroll-margin-top:16px }
  .hp-gl dd { margin:0; font-size:0.9rem; line-height:1.55 }
  .hp-gl dt.hit { color:var(--accent-text) }
  .gl-term { border-bottom:1px dotted currentColor; cursor:pointer; text-decoration:none }
  .hp-how { margin-left:8px; white-space:nowrap }
  .hp-how a { color:var(--accent-text); cursor:pointer; text-decoration:underline }
  .mf { margin-top:22px }
  .mf h2 { font-size:1.15rem; margin:0 0 4px }
  .mf-lead { color:var(--muted); font-size:0.88rem; margin:0 0 14px }
  .mf-tools { display:grid; grid-template-columns:repeat(3, 1fr); gap:12px }
  .mf-tool { background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:14px 16px; display:flex; flex-direction:column; gap:6px }
  .mf-tool.here { border-color:var(--accent) }
  .mf-name { font-weight:600; font-size:0.92rem }
  .mf-desc { color:var(--muted); font-size:0.82rem; line-height:1.45; flex:1 }
  .mf-foot { display:flex; justify-content:space-between; align-items:center; font-size:0.82rem; min-height:18px }
  .mf-foot a { color:var(--accent-text) }
  .mf-here { font-size:0.68rem; color:var(--accent-text); border:1px solid var(--accent); border-radius:10px; padding:1px 8px }
  .mf-support { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:16px; padding-top:14px; border-top:1px solid var(--line); font-size:0.85rem }
  .mf-support > div { flex:1; min-width:220px }
  .mf-btn { color:#fff !important; text-decoration:none; font-size:0.82rem; font-weight:600; border-radius:6px; padding:6px 16px }
  @media (max-width:760px) { .mf-tools { grid-template-columns:1fr } }
  @media (max-width:760px) { .hp { grid-template-columns:1fr } .hp-nav { position:static; max-height:none } }
"""

# The "More from me" section under every Help page (2026-10-01): one small list, so a fourth tool is a one-place change.
# `label` names the link (default "GitHub"); `here` marks this tool; a tool whose public page doesn't exist yet has url None (its card shows with no link).
MORE_TITLE = "More tools"
MORE_LEAD = "Free tools for a smoother modded Skyrim."
MORE_TOOLS = [
    {"name": "Vortex Collection Tools", "desc": "Everything you need to run massive Vortex collections without the friction.",
     "url": "https://github.com/awesmdiver/vortex-collection-tools", "here": False},
    {"name": "SkyrimNet MultiProxy", "desc": "Connect any AI to SkyrimNet—cloud or local, paid or free.",
     "url": "https://fateless.ai/plugins/awesmdiver/skyrimnet-multiproxy", "label": "Plugins page", "here": True},
    {"name": "ModPacer", "desc": "Automatic version checking and downloads for SkyrimNet Hub add-ons.",
     "url": "https://fateless.ai/plugins/awesmdiver/modpacer", "label": "Plugins page", "here": False},
]
MORE_TIP_BOLD = "Enjoying these?"
MORE_TIP_TEXT = "They're free and always will be. A tip keeps the coffee flowing."
MORE_TIPS = [
    {"name": "PayPal", "url": "https://paypal.me/awesmdiver", "color": "#00457C"},
    {"name": "Venmo", "url": "https://www.venmo.com/u/awesmdiver", "color": "#3D95CE"},
]


def _more_html() -> str:
    from html import escape as e
    cards = ""
    for t in MORE_TOOLS:
        link = f'<a href="{e(t["url"])}" target="_blank" rel="noopener">{e(t.get("label", "GitHub"))} ↗</a>' if t["url"] else ""
        tag = '<span class="mf-here">You&#39;re here</span>' if t["here"] else ""
        cards += (f'<div class="mf-tool{" here" if t["here"] else ""}"><div class="mf-name">{e(t["name"])}</div>'
                  f'<div class="mf-desc">{e(t["desc"])}</div><div class="mf-foot">{link}{tag}</div></div>')
    tips = "".join(f'<a href="{e(b["url"])}" target="_blank" rel="noopener" class="mf-btn" style="background:{b["color"]}">{e(b["name"])}</a>' for b in MORE_TIPS)
    return (f'<div class="card mf" id="moreFromMe"><h2>{e(MORE_TITLE)}</h2><p class="mf-lead">{e(MORE_LEAD)}</p>'
            f'<div class="mf-tools">{cards}</div>'
            f'<div class="mf-support"><div><b>{e(MORE_TIP_BOLD)}</b> <span class="lm-muted">{e(MORE_TIP_TEXT)}</span></div>{tips}</div></div>')


HELP_PANE_HTML = """
  <div id="paneHelp" style="display:none">
""" + tab_desc_ui.html("help") + """    <div class="card su-start" data-sh-static="help">
      <h3>Start here</h3>
      <ol class="su-steps">
        <li>Open the Help tab.</li>
        <li>Pick "Which AI should I use?" (first page).</li>
        <li>Pick your provider on the left and follow the steps.</li>
        <li>Click the button at the bottom to open its Providers card.</li>
      </ol>
    </div>
    <div class="hp">
      <div class="hp-nav"><input id="helpSearch" placeholder="Search help…" oninput="helpSearch(this.value)"><div id="helpNav"></div></div>
      <div class="hp-page" id="helpPage"><div class="lm-muted">Loading help…</div></div>
    </div>
  </div>
"""

HELP_JS = r"""// ---------------- Help tab ----------------
// Pages come from /help/content (built from docs/help/help-content.md); the dots and the top line are the providers' real state.
let helpTopics = null;
let helpCur = null;
let helpQuery = '';
let helpAnchor = null;   // a term inside the Glossary page to scroll to, e.g. 'provider'

async function helpLoad() {
  if (helpTopics) return;
  try {
    const r = await fetch('/help/content');
    helpTopics = (await r.json()).topics || [];
  } catch (e) { helpTopics = []; }
}
async function helpRefreshStatus() {
  try {
    const r = await fetch('/help/status');
    const st = await r.json();
    for (const p of PROVIDERS) if (st[p.id]) { p.state = st[p.id].state; p.status = st[p.id].status; }
  } catch (e) {}
}
function helpVisible(t) { return !t.provider || !!byId(t.provider); }
function helpMatches(t) {
  const q = helpQuery.trim().toLowerCase();
  return !q || t.title.toLowerCase().includes(q) || t.text.toLowerCase().includes(q);
}
async function helpOpened() {
  await helpLoad();
  helpRender();
  await helpRefreshStatus();
  helpRender();
  helpAnchor = null;
}
function helpRender() {
  if (!helpTopics) return;
  const list = helpTopics.filter(helpVisible);
  if (!helpCur || !list.some(t => t.id === helpCur)) helpCur = list.length ? list[0].id : null;
  const shown = list.filter(helpMatches);
  let nav = '', group = null;
  for (const t of shown) {
    if (t.group !== group) { group = t.group; nav += '<div class="hp-grp">' + esc(group) + '</div>'; }
    const p = t.provider ? byId(t.provider) : null;
    nav += '<a class="' + (t.id === helpCur ? 'on' : '') + '" onclick="helpSelect(\'' + t.id + '\')">' +
      (t.provider ? '<span class="hp-dot ' + (p && (p.state === 'ok' || p.state === 'warn') ? p.state : '') + '"></span>' : '') + esc(t.title) + '</a>';
  }
  if (!shown.length) nav = '<div class="lm-muted">Nothing matches that.</div>';
  $('helpNav').innerHTML = nav;
  const t = list.find(x => x.id === helpCur);
  if (!t) { $('helpPage').innerHTML = '<div class="lm-muted">Help couldn\'t load.</div>'; return; }
  const p = t.provider ? byId(t.provider) : null;
  let go = '';
  if (p && t.tab !== 'local' && t.tab !== 'other') go += '<button class="primary" onclick="helpGoSetup(\'' + t.provider + '\')">Open ' + esc(t.title) + ' on the Providers tab</button>';
  if (t.tab === 'local') go += '<button class="primary" onclick="showMainTab(\'local\')">Open the Local device tab</button>';
  if (t.tab === 'decisions') go += '<button class="primary" onclick="showMainTab(\'decisions\')">Open the Decisions tab</button>';
  if (t.tab === 'other') go += '<button class="primary" onclick="showMainTab(\'other\')">Open the Other devices tab</button>';
  if (t.tab === 'voice') go += '<button class="primary" onclick="showMainTab(\'voice\')">Open the Voice tab</button>';
  if (t.tab === 'quick') go += '<button class="primary" onclick="showMainTab(\'quick\')">Open the Quick Test tab</button>';
  if (t.tab === 'routing' && $('tabRouting')) go += '<button class="primary" onclick="showMainTab(\'routing\')">Open the Routing tab</button>';
  if (t.tab === 'speeds' && $('tabSpeeds')) go += '<button class="primary" onclick="showMainTab(\'speeds\')">Open the Speeds tab</button>';
  if (t.link) go += '<a href="' + esc(t.link.url) + '" target="_blank" rel="noopener"><button>' + esc(t.link.label) + ' ↗</button></a>';
  const status = p ? '<div class="hp-status"><span class="hp-dot ' + (p.state === 'ok' || p.state === 'warn' ? p.state : '') + '"></span>' + esc(p.status || '') + '</div>' : '';
  $('helpPage').innerHTML = status + '<h2>' + esc(t.title) + '</h2>' + t.html + (go ? '<div class="hp-go">' + go + '</div>' : '');
  if (helpAnchor) {
    const el = document.getElementById('gl-' + helpAnchor);
    const dt = el && el.tagName === 'SPAN' ? el.parentNode : el;
    if (dt && dt.scrollIntoView) { dt.scrollIntoView(); if (dt.classList) dt.classList.add('hit'); }
  }
}
function helpSelect(id, anchor) {
  helpCur = id;
  helpAnchor = anchor || null;
  try { history.replaceState(null, '', '#help/' + id + (anchor ? '/' + anchor : '')); } catch (e) {}
  helpRender();
  helpAnchor = null;
  const pg = $('helpPage'); if (pg && pg.scrollIntoView && window.innerWidth < 760) pg.scrollIntoView();
}
function helpSearch(v) { helpAnchor = null; helpQuery = v; helpRender(); }
function helpGoSetup(id) { showMainTab('setup'); selectProvider(id); }
function openHelp(id, anchor) {
  if (id) helpCur = id;
  helpAnchor = anchor || null;
  showMainTab('help');
  if (id) { try { history.replaceState(null, '', '#help/' + id + (anchor ? '/' + anchor : '')); } catch (e) {} }
}
function helpFromHash() {
  const m = /^#help(?:\/([\w-]+)(?:\/([\w-]+))?)?$/.exec(location.hash || '');
  if (!m) return false;
  openHelp(m[1] || null, m[2] || null);
  return true;
}
document.addEventListener('click', e => {
  const b = e.target && e.target.closest ? e.target.closest('#helpPage [data-copy]') : null;
  if (b) copyModel(b, b.getAttribute('data-copy'));
});
// a dotted-underline word on another screen opens its Glossary entry
document.addEventListener('click', e => {
  const g = e.target && e.target.closest ? e.target.closest('[data-gl]') : null;
  if (g) openHelp('glossary', g.getAttribute('data-gl'));
});
window.addEventListener('hashchange', helpFromHash);
// A first look with nothing set up lands on the Providers tab (its banner says what to do), not on a Help article.
if (!helpFromHash() && typeof HELP_FIRST_RUN !== 'undefined' && HELP_FIRST_RUN && !location.hash) showMainTab('setup');
"""
