"""Long model lists, grouped by model family (2026-10-01), built to design/dashboard-model-groups-mockup.html.

One shared piece for every provider card that lists models (Claude, Gemini, Codex, Google Antigravity,
Ollama, Local models, Other devices, Kilo, Nano-GPT, OpenAI, DeepSeek, GLM, TypeSafe, and anything added
later). A list with MORE THAN 5 models is shown as one row per family, newest model first; 5 or fewer
keep the flat list. Plain strings spliced into multiproxy.py's page (so no doubled braces); the logic part has
no DOM in it, so tests run it under Node with real model names.

How the family is worked out (see TECHNICAL.md, "Model families"):
  * Names-based providers (Claude, Gemini, Antigravity, Codex, OpenAI, DeepSeek, GLM, Nano-GPT, ...):
    from the display name, minus the maker word, the version, size and "(High)"-style tags.
        "Sonnet 5.5" -> Sonnet     "Gemini 3.1 Flash Lite Preview" -> Flash Lite     "GPT-5.6-Terra" -> Terra
  * Id-based providers (Ollama, Local models, Other devices): the base model name before the size/tag.
        "gemma4:26b" -> gemma4     "gemma-4-12b" -> gemma-4     "qwen3-coder:30b" -> qwen3-coder
  * Anything that leaves nothing -> "Other". Models you pinned (Codex) are a "Pinned" group, first.
"""

MODEL_GROUPS_CSS = r"""
  /* Grouped model lists (2026-10-01) -- mg-* classes, from design/dashboard-model-groups-mockup.html */
  .mgroup { border-top:1px solid var(--line) }
  .mgroup:first-of-type { border-top:none }
  .mg-row { display:grid; grid-template-columns:20px minmax(0,auto) minmax(0,1fr) auto; gap:12px; align-items:center; padding:10px 0; cursor:pointer }
  .mg-row:hover .mg-fam { color:var(--accent-text) }
  .mg-chev { color:var(--muted); font-size:0.75rem; display:inline-block; transition:transform .15s }
  .mgroup.open .mg-chev { transform:rotate(90deg) }
  .mg-fam { font-weight:600; font-size:0.9rem }
  .mg-count { color:var(--muted); font-size:0.78rem; margin-left:8px; font-weight:400 }
  .mg-desc { color:var(--muted); font-size:0.78rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .mg-items { display:none; padding:0 0 8px 32px }
  .mgroup.open .mg-items { display:block }
  .mg-items .model:first-of-type { border-top:none }
  @media (max-width:600px) { .mg-row { grid-template-columns:20px 1fr auto } .mg-desc { display:none } }
"""

# Pure logic: no DOM. `rows` are the provider's own [id, name, note, pinned?] rows.
MODEL_GROUPS_LOGIC_JS = r"""// ---------------- Model families (2026-10-01) ----------------
const MODEL_GROUP_THRESHOLD = 5;       // a list gets grouped when it has MORE than this many models

// Words that name the MAKER (or the product line), not the family: "Claude Sonnet" is Sonnet.
const MG_MAKER_WORDS = new Set(['claude', 'gemini', 'gpt', 'deepseek', 'glm', 'qwen', 'llama', 'mistral', 'grok', 'jev']);
// Words that only say how a model is offered, never which family it is.
const MG_NOISE_WORDS = new Set(['preview', 'latest', 'custom', 'tools', 'thinking', 'beta', 'experimental', 'exp']);
const MG_SIZE = /^\d+(\.\d+)?[bkm]$/i;                   // 120B, 7b, 270m
const MG_VERSION = /^v?\d+(\.\d+)*[a-z]?$/i;             // 5.5, V4, 4o
const MG_ID_STOP = /^(\d+(\.\d+)?[bkm]|a\d+b|it|instruct|chat|q\d.*|iq\d.*|f16|bf16|fp\d+|latest|cloud|gguf)$/i;
const MG_ID_PROVIDERS = new Set(['ollama', 'local', 'remote']);   // grouped from the model id, not a display name
// Lists that mix MAKERS (or have no real "newer"): version numbers can't be compared across them (Claude 4.6 is
// not "newer" than Gemini 3.8), so the groups keep the provider's own order instead of being sorted by version.
const MG_KEEP_ORDER = new Set(['geminicli', 'kilo', 'nano', 'ollama', 'local', 'remote']);

function mgTitle(s) {
  return s.split(' ').map(w => w === w.toLowerCase() ? w.charAt(0).toUpperCase() + w.slice(1) : w).join(' ');
}

// "Sonnet 5.5" -> Sonnet, "Gemini 3.1 Flash Lite Preview" -> Flash Lite, "GPT-5.6-Terra" -> Terra,
// "Claude Sonnet 4.6 (Thinking)" -> Sonnet, "GPT-OSS 120B (Medium)" -> GPT-OSS, "Qwen3.8 27B (uncensored)" -> Qwen.
function mgFamilyFromName(name) {
  const bare = String(name || '').replace(/\([^)]*\)/g, ' ');
  let tokens = bare.split(/[\s\-_]+/).filter(Boolean);
  if (tokens.length >= 2 && tokens[0].toLowerCase() === 'gpt' && tokens[1].toLowerCase() === 'oss') {
    tokens = ['GPT-OSS'].concat(tokens.slice(2));
  }
  const kept = [];
  for (let t of tokens) {
    const low = t.toLowerCase();
    if (MG_NOISE_WORDS.has(low) || MG_SIZE.test(t)) continue;
    if (MG_VERSION.test(t) && /\d/.test(t)) continue;
    const glued = t.match(/^([A-Za-z]{3,}?)v?\d[\d.]*[a-z]?$/);     // "Qwen3.8", "GLM4", "gemma4" -> the letters ("R1", "o3" stay)
    if (glued) t = glued[1];
    kept.push(t);
  }
  let words = kept;
  if (words.length > 1 && MG_MAKER_WORDS.has(words[0].toLowerCase())) words = words.slice(1);
  const family = mgTitle(words.join(' ')).trim();
  return family || 'Other';
}

// "ollama/gemma4:26b" -> gemma4, "local/gemma-4-12b" -> gemma-4, "den-pc/qwen3-coder:30b" -> qwen3-coder.
function mgFamilyFromId(mid) {
  let base = String(mid || '');
  base = base.slice(base.lastIndexOf('/') + 1);          // drop ollama/ local/ <nickname>/ and any maker/ path
  base = base.split(':')[0];
  const out = [];
  for (const t of base.split('-')) {
    if (MG_ID_STOP.test(t)) break;
    out.push(t);
  }
  return out.join('-') || 'Other';
}

function mgFamily(providerId, row) {
  return MG_ID_PROVIDERS.has(providerId) ? mgFamilyFromId(row[0]) : mgFamilyFromName(row[1] || row[0]);
}

// The first version-looking number in a name (or id): "Opus 4.5" -> [4,5], "gemma4" -> [4], none -> [].
function mgVersion(row, providerId) {
  const text = MG_ID_PROVIDERS.has(providerId) ? String(row[0]).slice(String(row[0]).lastIndexOf('/') + 1) : (row[1] || row[0]);
  for (const t of String(text).split(/[\s\-_:\/()]+/)) {
    if (MG_SIZE.test(t) || /^\d{6,}$/.test(t)) continue;
    const m = t.match(/^[A-Za-z]*?v?(\d+(?:\.\d+)*)[a-z]?$/);
    if (m && /\d/.test(t)) return m[1].split('.').map(Number);
  }
  return [];
}
function mgCompareVersions(a, b) {     // > 0 when a is newer than b; a missing version is oldest
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const d = (a[i] === undefined ? -1 : a[i]) - (b[i] === undefined ? -1 : b[i]);
    if (d !== 0) return d;
  }
  return 0;
}
function mgIsPreview(row) { return /preview|beta|exp/i.test((row[1] || '') + ' ' + (row[2] || '')); }

// Newest first. Ties keep the provider's own order, except that a finished model beats a "preview" one
// and Ollama's `:latest` beats other tags.
function mgSortNewestFirst(rows, providerId) {
  const decorated = rows.map((row, i) => ({ row, i, v: mgVersion(row, providerId) }));
  decorated.sort((a, b) =>
    mgCompareVersions(b.v, a.v)
    || (mgIsPreview(a.row) - mgIsPreview(b.row))
    || ((/:latest$/.test(b.row[0]) ? 1 : 0) - (/:latest$/.test(a.row[0]) ? 1 : 0))
    || (a.i - b.i));
  return decorated.map(d => d.row);
}

// -> [{ family, pinned, newest, rows }], Pinned first, then families ordered by their newest model.
function mgGroupRows(providerId, rows) {
  const pinned = rows.filter(r => r[3]);
  const plain = rows.filter(r => !r[3]);
  const byFamily = new Map();
  plain.forEach((row, i) => {
    const fam = mgFamily(providerId, row);
    if (!byFamily.has(fam)) byFamily.set(fam, { family: fam, first: i, rows: [] });
    byFamily.get(fam).rows.push(row);
  });
  const groups = [...byFamily.values()].map(g => {
    const sorted = mgSortNewestFirst(g.rows, providerId);
    return { family: g.family, pinned: false, newest: sorted[0], rows: sorted, first: g.first };
  });
  if (!MG_KEEP_ORDER.has(providerId)) {
    groups.sort((a, b) => mgCompareVersions(mgVersion(b.newest, providerId), mgVersion(a.newest, providerId)) || (a.first - b.first));
  }
  // "Other" is the catch-all: always last, so it never outranks a real family
  groups.sort((a, b) => (a.family === 'Other') - (b.family === 'Other'));
  const out = pinned.length ? [{ family: 'Pinned', pinned: true, newest: null, rows: pinned }] : [];
  return out.concat(groups);
}
"""

# DOM part: builds the HTML and handles opening/closing.
MODEL_GROUPS_RENDER_JS = r"""
const openModelGroups = new Set();     // remembered while the page stays open, not across restarts

function toggleModelGroup(rowEl, key) {
  const group = rowEl.parentElement;
  const open = group.classList.toggle('open');
  if (open) openModelGroups.add(key); else openModelGroups.delete(key);
}

// rowHtml(row, inGroup) draws one model row; inGroup=true means "leave the description off" (it's on the
// group row). `listKey` tells two lists apart (e.g. each Codex provider) so their open groups don't mix.
function renderModelListHTML(providerId, listKey, rows, rowHtml) {
  if (rows.length <= MODEL_GROUP_THRESHOLD) return rows.map(r => rowHtml(r, false)).join('');
  return mgGroupRows(providerId, rows).map(g => {
    const key = (listKey + '|' + g.family).replace(/['"<>&\\]/g, '');     // the same safe key is stored and looked up
    const open = openModelGroups.has(key) ? ' open' : '';
    const count = g.rows.length + (g.rows.length === 1 ? ' model' : ' models');
    const desc = g.newest && g.newest[2] ? `<span class="mg-desc" title="${_escapeHtml(g.newest[2])}">${_escapeHtml(g.newest[2])}</span>` : '<span class="mg-desc"></span>';
    const copy = g.newest
      ? `<button class="copy" title="Copies ${_escapeHtml(g.newest[0])}" onclick="event.stopPropagation(); copyModel(this, '${jsq(g.newest[0])}')">Copy newest</button>`
      : '<span></span>';
    return `<div class="mgroup${open}"><div class="mg-row" onclick="toggleModelGroup(this, '${key}')">` +
      `<span class="mg-chev">▶</span><span class="mg-fam">${_escapeHtml(g.family)}<span class="mg-count">${count}</span></span>${desc}${copy}</div>` +
      `<div class="mg-items" onclick="event.stopPropagation()">${g.rows.map(r => rowHtml(r, true)).join('')}</div></div>`;
  }).join('');
}
"""
