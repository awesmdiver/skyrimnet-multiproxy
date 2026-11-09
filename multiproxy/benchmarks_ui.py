"""The dashboard's Benchmarks tab (design/mockup-model-performance.html, sections 1 to 5 and revision 3: 8a, 8b, 8c, 8e; Chat only, this PC only).

One page: the progress of a running test above three parts, in order: pick the models, set up the test, the results (and, when a row is opened, that run's detail page). The words are final
(design/mockup-benchmarks-words.html, the Proposed column); they all live in `BM_WORDS` in one place. Every number on the page comes from the result files through
`/config/benchmarks/results` and `.../runs/<id>/detail`; one that cannot be measured reads "Not measured" (the page never draws a dash that looks like zero), an estimate says so, and nothing
is scored, ranked or coloured except the headroom dot and the run-status dot. The HTML builders are pure functions of the data (`bmModelsHtml`, `bmRunHtml`, `bmResultsHtml`,
`bmDetailHtml`, `bmPastHtml`) so each screen state is tested with made-up data. The setup form has its own container that polling never redraws (DESIGN.md: a text box never loses focus).
The tab is quiet when idle: nothing is polled while the tab is not shown, a cheap status check every 5 s while it is, a faster one while a test runs or a download is going.
"""

import tab_desc_ui

BENCHMARKS_TAB_HTML = """<div class="tab" id="tabBenchmarks" onclick="showMainTab('benchmarks')">Benchmarks</div>"""

BENCHMARKS_PANE_HTML = """
  <div id="paneBenchmarks" style="display:none">
    """ + tab_desc_ui.html("benchmarks") + """<p class="lm-hint"><span class="hp-how"><a onclick="openHelp('benchmarks')">How does this test work?</a></span></p>
    <div class="card"><h3 class="bm-h bm-foldhead" id="bmPickHead" onclick="bmFoldToggle('pick')"><span class="bm-arrow">▼</span>Pick the models to test</h3><div id="bmModels"><div class="lm-muted">Looking at your models…</div></div></div>
    <div class="card"><h3 class="bm-h bm-foldhead" id="bmSetupHead" onclick="bmFoldToggle('setup')"><span class="bm-arrow">▼</span>Set up the test</h3><div id="bmEngine"></div><div id="bmSetup"></div></div>
    <div id="bmRun"></div>
    <div class="card"><div id="bmResults"></div></div>
    <div class="card" id="bmPastCard" style="display:none"><div id="bmPast"></div></div>
  </div>
"""

BENCHMARKS_CSS = r"""
  /* Benchmarks tab -- bm-* classes; the card, ov-table, ov-dot, lm-* text, op-modal pop-up and the buttons are reused */
  .bm-h { margin:0 0 10px; font-size:1.05rem } .bm-h2 { margin:18px 0 6px; font-size:0.92rem }
  .bm-wrap { overflow-x:auto }
  .bm-table { width:100%; table-layout:fixed }
  .bm-table td, .bm-table th { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  .bm-table td.bm-wrapok { white-space:normal }
  .bm-table.bm-res { table-layout:auto } .bm-res th { white-space:normal; vertical-align:bottom; padding:8px 4px } .bm-res { font-size:0.78rem } .bm-res th { font-size:0.72rem } .bm-res .ov-dot { margin-right:4px } .bm-res td { padding:8px 4px; white-space:normal } .bm-res td.bm-nm, .bm-res td.bm-tickcell { white-space:nowrap } .bm-res td.bm-nm { max-width:7.5rem } .bm-res .bm-small { white-space:normal } 
  .bm-textact { background:none; border:none; box-shadow:none; color:var(--accent-text); padding:0 4px; font-size:0.78rem; cursor:pointer; min-height:0 }
  .bm-textact:hover { text-decoration:underline }
  .bm-table td.bm-tickcell, .bm-table th.bm-tickcell { overflow:visible; text-overflow:clip }
  .bm-slots { display:flex; gap:8px; justify-content:flex-end } .bm-slot { width:6.2rem; display:flex; justify-content:flex-end } .bm-slot button { min-width:5.6rem }
  .bm-table .r { text-align:right } .bm-table th.r { text-align:right }
  .bm-table th.bm-tip, .bm-tip { cursor:help }
  .bm-name { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; background:none; border:0; padding:0; color:var(--text); text-align:left; cursor:pointer; width:100%; font-size:inherit }
  .bm-name:hover { background:none; color:var(--accent-text) }
  .bm-big { display:block } .bm-small { display:block; color:var(--muted); font-size:0.74rem }
  .bm-dl { display:flex; flex-direction:column; gap:3px; height:3.3rem; justify-content:center; overflow:hidden }
  .bm-dl > span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:block; min-height:1.1rem }
  .bm-dl .lm-meta { margin-top:0 } .bm-dl .bm-nobar { height:6px }
  .bm-row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin:4px 0 }
  .bm-q { margin:16px 0 4px; font-weight:600; font-size:0.9rem }
  .bm-hint { color:var(--muted); font-size:0.82rem; margin:2px 0 0 }
  .bm-opt { display:flex; gap:8px; align-items:baseline; margin:5px 0 }
  .bm-opt input[type=number] { width:6.5rem }
  .bm-num { width:6.5rem }
  .lm-dot.bm-fit-big { color:var(--err) }
  .bm-btns { display:flex; gap:8px; justify-content:flex-end; margin-top:14px; flex-wrap:wrap }
  .bm-fails summary { cursor:pointer; margin:10px 0 4px; font-size:0.85rem } .bm-failist { margin:2px 0 8px; padding-left:1.2rem; font-size:0.82rem } .bm-failist li { margin:4px 0 } .bm-line { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .bm-adv summary { cursor:pointer; margin:14px 0 6px; font-weight:600; font-size:0.9rem }
  .bm-adv table td { white-space:normal } .bm-adv td:first-child { width:14rem } .bm-adv td:nth-child(2) { width:16rem }
  .bm-adv input, .bm-adv select { max-width:14rem }
  .bm-tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:12px 0 }
  .bm-tile { border:1px solid var(--line); border-radius:8px; padding:10px 12px; min-height:4.6rem; overflow:hidden }
  .bm-tile .bm-k { color:var(--muted); font-size:0.76rem } .bm-tile .bm-v { font-size:1.15rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis } .bm-tile .bm-s { color:var(--muted); font-size:0.74rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .bm-bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin:8px 0 }
  .bm-bar i { display:block; height:6px; background:var(--accent) }
  .bm-step { min-height:1.3rem }
  .bm-cbar { display:flex; height:8px; border-radius:4px; background:var(--line); overflow:hidden; margin:6px 0 }
  .bm-cbar i { display:block; height:8px; background:var(--accent) }
  .bm-steprow { display:flex; justify-content:space-between; gap:4px 16px; flex-wrap:wrap; align-items:baseline; margin-top:4px }
  .bm-cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px 32px; margin:2px 0 10px }
  .bm-colhead { font-size:0.72rem; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:2px }
  .bm-cols .bm-small { margin:0 } .bm-cols .lm-warn { margin:0; font-size:0.74rem }
  .bm-key { display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:6px; background:var(--accent) }
  .bm-k1 { background:var(--accent); opacity:1 } .bm-k2 { background:var(--accent); opacity:0.6 } .bm-k3 { background:var(--accent); opacity:0.3 } .bm-k4 { background:var(--muted); opacity:0.7 }
  .bm-cbar i.bm-k1 { opacity:1 } .bm-cbar i.bm-k2 { opacity:0.6 } .bm-cbar i.bm-k3 { opacity:0.3 } .bm-cbar i.bm-k4 { background:var(--muted); opacity:0.7 }
  .bm-live2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin:10px 0 0 }
  .bm-live2 .bm-table td { white-space:nowrap; overflow:hidden; text-overflow:ellipsis } .bm-live2 .bm-table td.r, .bm-card .bm-table td.r { text-align:right }
  .bm-live2 .bm-table { table-layout:fixed }
  .bm-card .bm-table { table-layout:fixed } .bm-card .bm-table td { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .bm-foldhead { display:flex; align-items:center; gap:10px; cursor:pointer }          /* the dashboard's folded-card heading: the same arrow and click target as the Other devices cards */
  .bm-arrow { color:var(--muted); width:12px; font-size:0.75rem }
  .bm-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap }
  .bm-facts { color:var(--muted); font-size:0.82rem; overflow-wrap:anywhere }
  .bm-charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:16px; margin:12px 0 }
  .bm-chart svg { width:100%; height:38px; display:block }
  .bm-chart svg polyline { fill:none; stroke:var(--accent); stroke-width:1.6; vector-effect:non-scaling-stroke }
  .bm-chart b { display:block }
  .bm-msg { margin:10px 0 0 } .bm-msg:empty { display:none }
  .bm-note { margin:8px 0 0 }
  .bm-note:empty { display:none }
  .bm-hflink { text-decoration:none } .bm-hflink:hover { text-decoration:underline }
  .bm-right { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; margin-top:12px }
  .bm-st { white-space:nowrap }
  .bm-foot { color:var(--muted); font-size:0.8rem; margin:8px 0 0 }
  .bm-legend { color:var(--muted); font-size:0.8rem; display:flex; flex-wrap:wrap; align-items:center; gap:4px 14px; margin:0 0 10px } .bm-legend .bm-legend-item { white-space:nowrap }
  /* a narrow window: the buttons of a model row go under its name; the size and status stay on the name's line */
  @media (width <= 820px) {
    .bm-models, .bm-models tbody { display:block }
    .bm-models colgroup, .bm-models th:nth-child(4), .bm-models td:nth-child(4), .bm-models th:nth-child(6) { display:none }
    .bm-models tr { display:grid; grid-template-columns:2.4rem minmax(0,1fr) auto auto; gap:2px 10px; align-items:center; border-bottom:1px solid var(--line) }
    .bm-models td, .bm-models th { display:block; width:auto; border-bottom:0 }
    .bm-models td:nth-child(6) { grid-column:2 / -1; padding-top:0 }
    .bm-models .bm-slots { justify-content:flex-start; flex-wrap:wrap } .bm-models .bm-slot { width:auto } .bm-models .bm-slot:empty { display:none }
  }
  .bm-custom { font-size:0.8rem; color:var(--muted); margin-left:8px }
"""

BENCHMARKS_JS = r"""// ---------------- Benchmarks tab (test models on this PC, measured, never scored) ----------------
const BM_WORDS = {
  vramTip: 'VRAM: your graphics memory',
  models: { model: 'Model', size: 'Size', from: 'Where it comes from', status: 'Status', note: 'These starter models come from the AI site Hugging Face. Select which models you would like to benchmark.', local: 'Local file', downloaded: 'Downloaded', notDownloaded: 'Not downloaded',
    onThisPc: 'On this PC', missing: 'File not found', addFile: 'Add an LLM model (.gguf)', download: 'Download', remove: 'Remove', pause: 'Pause', resume: 'Resume', cancel: 'Cancel',
    none: 'No models in the list yet.', selectAll: 'Select all', downloadTicked: 'Download ticked', willDownload: 'Will download first',
    fitLegend: 'Too big or a tight fit (hover to see why)',
    fitBigNum: (n, x, y) => 'Too big for this card at ' + n + ' at once: about ' + x + ' GB needed, ' + y + ' GB free.', fitTightNum: (n, x, y) => 'About ' + x + ' GB needed at ' + n + ' at once, ' + y + ' GB free.',
    fitRough: 'The size is a rough estimate for this model.',
    fitAskSentence: (name, x, n, y, where) => name + ' needs about ' + x + ' GB of graphics memory at ' + n + ' requests at once, but only ' + y + ' GB is free ' + where + ". It will spill into the computer's main memory, which can make the PC very slow or unresponsive.",
    fitAskNow: (name, x, n, y, where, m, c, names, used) => name + ' needs about ' + x + ' GB of graphics memory at ' + n + ' requests at once, but only ' + y + ' GB is free ' + where + '.' +
      (m != null && c != null ? ' That is an estimate: about ' + m + ' GB for the model and ' + c + ' GB for the cache, plus some working space.' : '') +
      (names.length ? ' Right now ' + (names.length > 1 ? names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1] : names[0]) + (names.length > 1 ? ' are' : ' is') + ' using ' + used + ' GB of the card.' : '') +
      " It will spill into the computer's main memory, which can make the PC very slow or unresponsive.",
    fitHoverNow: (x, n, f) => 'Needs about ' + x + ' GB at ' + n + ' at once. ' + f + ' GB are free right now.',
    fitAskHere: 'here', fitAskThere: 'on that PC', fitAskCancel: 'Cancel', fitAskLeave: 'Leave it out', fitAskRun: 'Run anyway',
    fitTight: 'Tight fit alongside Skyrim.', fitBig: 'Too big to run alongside Skyrim.', fitTightCard: 'A tight fit on this device.', fitBigCard: 'Too big for this device.',
    needDl: (n, size) => n === 1 ? '1 ticked needs downloading (' + size + '). Start benchmark downloads it first.' : n + ' ticked need downloading (' + size + '). Start benchmark downloads them first.',
    paused: 'Paused', downloading: 'Downloading', removeLocal: 'Remove from the list', removeAsk: n => 'Remove ' + n + ' from the list? The file stays where it is.' },
  setup: { concurrency: 'Concurrency', concurrencyHint: 'We test each level separately by sending that many concurrent requests. SkyrimNet often asks for several things at once, so this shows how the model handles overlapping work.',
    rpl: 'Requests per level', rplHint: n => 'The test runs ' + n + ' measured requests per concurrency level. The two warm-up requests we send first are saved but kept out of the final stats.',
    prompt: 'Prompt length', promptHint: 'We use fixed prompts at three lengths, modelled on SkyrimNet requests.', short: 'Short', medium: 'Medium', long: 'Long',
    skyrim: 'Skyrim and ', history: 'Use Skyrim play history', closed: 'Game closed (assumes Skyrim isn\'t using VRAM)', typed: 'Enter available VRAM:', typedLabel: 'VRAM available to the model (GB)',
    historyLine: (gb, day) => 'Skyrim VRAM: peaked at ' + gb + ' GB' + (day ? ' (' + day + ')' : '') + '. The fit estimate uses your recorded peak.',
    noHistory: ['Leave MultiProxy running.', 'Play Skyrim for a few minutes.', 'Come back here to fill in your history.'], noHistoryOr: 'Or pick Game closed, or enter your available VRAM, to test right now.',
    reset: 'Reset tracker', between: 'Between models', cool: 'Cool-down between models',
    coolPre: 'we wait up to 5 minutes for the GPU to drop below', coolPost: '°C and record the starting temperature. This gives each model a similar start; it can\'t make conditions identical.',
    safety: 'Safety', memGuard: "Stop testing if the PC runs short of memory.", memGuardNote: 'Always stops if memory is almost gone.', heat: 'Stop the test if the GPU reaches its reported temperature limit. The result logs a thermal stop.',
    gpus: 'GPUs', gpusHint: 'Automatic, or pick the GPU to test on. The results show where the model was placed and the memory each GPU used.', automatic: 'Automatic',
    advanced: 'Advanced: change test settings', setting: 'Setting', value: 'Value', why: 'Why change this?',
    ctx: 'Context size', ctxWhy: 'Bigger remembers more, uses more memory.', layers: 'GPU layers', layersWhy: 'How much of the model sits on the card.',
    flash: 'Flash attention', flashWhy: 'Faster and lighter on some cards.', batch: 'Batch size', batchWhy: 'Affects reading speed and memory.',
    threads: 'CPU threads', threadsWhy: 'Only matters when part of the model runs on the processor.', temp: 'Temperature', tempWhy: 'Fixed during testing to keep format checks steady (this does not affect speed).',
    kv: 'Context memory type (KV cache)', kvWhy: 'Smaller types use less memory.', sustained: 'Sustained run length (minutes)', sustainedWhy: 'The usual time is 5 minutes.', slots: 'Parallel slots', slotsValue: 'Matches Concurrency. It cannot be changed separately, so 4 concurrent requests means 4 slots.',
    usual: 'Usual', auto: 'Automatic', all: 'All', none: 'None', number: 'A number', on: 'On', off: 'Off',
    saved: 'All your settings are saved with the results. Runs only compare fairly if the model, workload and these settings match.', resetDefaults: 'Reset to defaults',
    custom: 'Custom', customLine: 'Custom run: changed settings mean this will not be compared to standard runs.',
    heavy: 'Benchmarking puts a heavy load on your GPU. Expect about 6 minutes per model. Don\'t play Skyrim during a game-closed test. The test may stop models MultiProxy started on this device.',
    start: 'Start benchmark', stop: 'Stop benchmark', pickModel: 'Tick at least one downloaded model first.', pickLevel: 'Tick at least one concurrency level.', pickPrompt: 'Tick at least one prompt length.',
    stopIt: 'Stop and run', needModel: 'Pick the models to test.', noEngine: "Benchmarks need the local engine (llama.cpp). Install it from the Local device tab.", openLocal: 'Open Local device',
    modelsOff: 'Local models are turned off on this device, and Benchmarks need them.', setUpLocal: 'Set up local models' },
  card: { title: 'On this card right now', refresh: 'Refresh', free: 'Free for the test', more: n => n + ' more' },
  amd: { before: 'AMD cards are new in MultiProxy and not fully tested. If something looks wrong, please tell us on the project\'s ', link: 'Issues page', after: '.', url: 'https://github.com/awesmdiver/skyrimnet-multiproxy/issues' },
  facts: { layers: n => n + ' layers', maxCtx: c => 'up to ' + c + ' context', factsTip: 'Read from the model file.',
    cacheLine: parts => 'Cache: ' + parts.join(', '), cachePart: (ctx, size) => ctx + ' context = ' + size,
    cacheTip: 'About how much memory the conversation cache takes for one request at that size. Our estimate from the model file.',
    engine: (name, build, backend) => 'Engine: ' + [name, build].filter(Boolean).join(' ') + (backend ? ' (' + backend + ')' : ''), queue: (b, n) => 'Queue: ' + b + ' of ' + n + ' requests busy now',
    archLabel: 'Architecture', sizeLabel: 'Size', cacheEstLabel: 'Cache Est', specsHead: 'Model specs', statusHead: 'Live status',
    engineTip: 'Read from the engine itself.', spill: size => 'VRAM spill: ' + size,
    spillTip: 'Memory that belongs to the card but is held in regular memory right now. It slows the model down.' },
  run: { modelTitle: 'The model', cacheAt: n => 'The cache (' + n + ' at once)', workSpace: 'Working space', measuredWord: 'measured', elseTitle: 'Everything else',
    model: (k, n, name) => 'Model ' + k + ' of ' + n + ': ' + name, step: (k, n, what) => 'Step ' + k + ' of ' + n + ': ' + what, vram: 'VRAM', util: 'GPU utilization', temp: 'GPU temperature', power: 'GPU power draw', clock: 'GPU clock',
    cpu: 'CPU utilization', ram: 'System RAM used', notMeasured: 'Not measured', highestSoFar: 'Highest so far', highest: 'Highest',
    starting: 'Getting ready', hashing: p => 'Checking the model file' + (p != null ? ' (' + p + '%)' : ''), load: 'Loading the model', warmup: 'Warming up', restart: n => 'Restarting the model for ' + n + ' concurrent requests',
    level: n => 'Generation, ' + n + ' concurrent request' + (n === 1 ? '' : 's'), sustained: '5-minute sustained run', unload: 'Unloading', cooldown: 'Cooling', finished: 'Finishing',
    readings: 'Readings are sampled once a second.', elapsed: 'Elapsed', thisModel: 'This model', stoppedLive: 'This model was stopped to protect your PC. Moving to the next.', partCpu: 'Part of this model runs on the processor. This may make responses slower.', waitingMemory: "Waiting" },
  res: { total: 'Total', title: 'Results', model: 'Model', size: 'Size', peakVram: 'Peak VRAM used', memBreakdown: 'Memory breakdown', estBefore: v => 'estimated before the run: ' + v, headroom: 'Est. VRAM headroom', load: 'Load time', prompt: 'Prompt processing (tok/s)', gen: 'Generation (tok/s)',
    ttft: n => 'TTFT at ' + n + ' concurrent', temp: 'Peak GPU temp', sustained: '5-minute sustained run', checks: 'Automated checks', status: 'Run status', none: 'No results yet. Select a model above and click Start benchmark.',
    peakTip: n => 'The most VRAM the model used. This is the card\'s peak use minus what was used before the model loaded.', sustainedTip: 'Runs the model for five minutes straight, like a long play session, to see if it slows down as the card heats up. "Not run" means this part was skipped or stopped.', headroomTip: 'Our best guess at the VRAM left after subtracting Skyrim\'s recorded peak and the model\'s peak. Skyrim can spike higher in new areas, and other apps can still use VRAM.',
    legendLabel: 'Est. VRAM headroom:', legend: [['ok', '4 GB or more'], ['warn', 'under 4 GB'], ['bad', 'Not enough free space'], ['off', 'not enough data']],
    notEnough: 'Not enough data', testAgain: 'Test again', add: 'Add to MultiProxy', view: 'View settings', exportAll: 'Export results', notMeasured: 'Not measured', notReported: 'Not reported',
    of: n => 'of ' + n, slowest: (v, n) => v + ' (95th slowest of ' + n + ')', min: 'shortest', max: 'longest',
    completed: 'Completed', failed: 'Failed', interrupted: 'Interrupted', cancelled: 'Cancelled', invalidated: 'Invalidated', running: 'Running',
    stoppedMemory: 'Stopped to protect your PC: memory was running low', stoppedMemoryAt: n => 'Memory ran low at ' + n + ' at once', stoppedMemoryEarly: 'Memory ran low while loading',
    stoppedTime: 'Stopped: it took too long', stoppedTimeAt: min => 'Over ' + min + ' minutes', partial: 'Partial result: test stopped', stopsSummary: n => (n === 1 ? '1 model was' : n + ' models were') + ' stopped to protect your PC.', newer: 'Saved by a newer version of MultiProxy',
    stepsDone: (a, b) => a + ' of ' + b + ' steps done', spill: 'VRAM spill detected', spillTip: 'The model had to use system RAM, so speeds will be well below a run that fits entirely in VRAM.',
    sustCompleted: 'Held its speed', sustSlow: n => 'Slowed down after ' + n + ' min', sustLowHeld: n => 'lowest minute at ' + n + '% of the start', sustLowSlow: n => 'down to ' + n + '% of the start',
    sustTip: (a, b) => 'Speed in the first minute: ' + a + ' tok/s. Lowest later minute: ' + b + ' tok/s.', sustFailed: 'Failed', sustInterrupted: 'Interrupted', sustNotRun: 'Not run',
    reason: { thermal_stop: 'The GPU reached its temperature limit.', program_stopped: 'MultiProxy was closed', stopped: 'it was stopped', memory_emergency: 'memory was almost gone', memory_low: 'memory ran low',
      memory_repeated: 'memory ran low a second time', memory_held: 'memory stayed low', commit_pressure: 'the PC was running out of memory to hand out', memory_spill: 'the model spilled out of the card into system memory', too_long: 'it took too long' }, added: 'Added to the Models tab.', addedAlready: 'That model is already on the Models tab.',
    tickFirst: 'Tick a model first.', custom: 'Custom' },
  det: { back: 'Return', concurrency: 'Concurrency', atOnce: 'At once', requests: 'Requests', ttftAvg: 'TTFT avg', ttftP: 'TTFT p50 / p95', ttftRange: 'TTFT shortest – longest', genAvg: 'Generation (tok/s) avg',
    genRange: 'Generation (tok/s) shortest – longest', promptAvg: 'Prompt processing (tok/s) avg', together: 'All requests together /s', failed: 'Failed',
    pNote: 'p50 is the median. p95 means 95% of requests were this fast or faster. "All requests together" is the total tokens per second across all concurrent requests.',
    promptLength: n => 'Prompt length (' + n + ' concurrent requests)', prompt: 'Prompt', tokensIn: 'Tokens in', readTime: 'Time to read', tokensOut: 'Tokens out (avg)', ttft: 'TTFT', gen: 'Generation (tok/s)', promptSpeed: 'Prompt processing (tok/s)',
    viewFailed: 'View failed checks', andMore: n => 'and ' + n + ' more', modelSettings: 'Model and settings', reading: 'Setting or reading', value: 'Value', layers: 'Layers on the card', ctx: 'Context size / per request', kv: 'Context memory (KV cache), type', flash: 'Flash attention',
    batch: 'Batch size / micro-batch', threads: 'CPU threads', ram: 'System RAM used: before / highest', loadTime: 'Load time (disk to ready)', gpuChoice: 'GPU used',
    startedWith: 'On the card when it started', memory: 'Memory', skyrimRow: 'Skyrim, from your history', modelRow: 'This model, measured in the test', headroomRow: 'Est. VRAM headroom', what: 'What',
    skyrimHistory: mb => 'peak ' + mb, skyrimClosed: 'Game closed (assumes Skyrim isn\'t using VRAM)', skyrimTyped: 'VRAM available to the model: typed in', skyrimNone: 'No Skyrim history',
    cardHighest: g => 'Highest on the whole card: ' + g, vramChart: 'VRAM in use on the card',
    modelMem: (b, l, h, n) => 'before ' + b + ' · loaded ' + l + ' · highest' + (n ? ' at ' + n + ' concurrent' : '') + ' ' + h, headroomAt: v => v + ' at the peak',
    telemetry: 'GPU and system telemetry', lowest: 'Lowest', average: 'Average', highestWord: 'Highest', checks: 'Automated checks', check: 'Check', passed: 'Passed', of: 'Of',
    genChart: n => 'Generation (tok/s), request by request (' + n + ')', request: n => 'request 1 → ' + n, wholeTest: 'whole test', exportCsv: 'Export requests (CSV)', exportJson: 'Export full results (JSON)', copy: 'Copy summary', again: 'Repeat this test',
    fingerprint: 'file fingerprint', setup: 'test setup', run: 'test run', testSet: 'test set', engine: 'engine', driver: 'driver', ended: 'ended', testTime: 'Test time', noHeatLimit: 'This card reports no heat limit, so the automatic heat stop is off.',
    heatStopped: 'The test stopped because the GPU reached its own temperature limit.', spillObs: o => o, readings: { temp_c: 'GPU temperature', gpu_util_pct: 'GPU utilization', clock_mhz: 'GPU clock', power_w: 'GPU power draw', fan_pct: 'Fan speed', cpu_util_pct: 'CPU utilization' },
    gpu: n => 'GPU ' + n, computer: 'This PC', saveFailed: 'Couldn\'t save the file.', copied: 'Copied' },
  past: { title: 'Past runs', open: 'Open', del: 'Delete', delN: n => 'Delete (' + n + ')', selectAll: 'Select all', invert: 'Invert', clear: 'Clear', askMany: 'Are you sure?', ask: n => 'Delete the test of ' + n + '? Its saved results are removed from this PC.', go: 'Delete' },
};
let bmCard = null, bmTab = null, bmStatus = null, bmResults = null, bmDetail = null, bmView = 'list', bmBusy = false, bmTimer = null, bmErr = '', bmMsg = '', bmTicks = {}, bmPastTicks = {}, bmPastIds = [], bmSetupBuilt = '';
let bmPoll = null, bmWasRunning = false, bmSaveTimer = null, bmLastStep = '';
const BM_DEFAULT_SET = { levels: [1, 4, 8], rpl: 25, prompts: ['short', 'medium', 'long'], skyrim: 'history', typed: '', cool: true, coolC: 55, heat: true, mem: true, gpu: '',
  adv: { ctx: '', layers: '', layersN: '', flash: '', batch: '', threads: '', temp: '0', cache: '', sustained: '' } };
let bmSet = JSON.parse(JSON.stringify(BM_DEFAULT_SET));

// ---- small helpers (numbers are formatted here; where a number comes from is the server's job) ----
function bmNum(n, d) { return Number(n).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
function bmGb(mb) { return mb == null ? null : bmNum(mb / 1024, 1) + ' GB'; }
function bmLeft(sec) {
  if (typeof sec !== 'number' || !isFinite(sec) || sec < 0) return '';
  if (sec < 60) return '< 1 min';
  const m = Math.round(sec / 60);
  return m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
}
// "4.5 of 5.3 GB · < 1 min": one line; the time is left out while there is none to give (or says "waiting for network" when the download is stalled)
function bmDlLine(d) {
  const sizes = bmGB(d.done) + ' of ' + bmBytes(d.total), t = bmLeft(d.leftSeconds) || (d.left || '');
  return sizes + (t ? ' · ' + t : '');
}
function bmGB(b) { return b >= 1e8 ? (b / 1e9).toFixed(1) : bmNum(b / 1e6) + ' MB'; }
function bmDlBox(dot, label, pct, line) {
  return '<div class="bm-dl"><span title="' + esc(label) + '">' + ovDot(dot) + esc(label) + '</span>' +
    (pct == null ? '<div class="bm-nobar"></div>' : '<div class="lm-bar" style="margin:0;width:100%"><i style="width:' + pct + '%"></i></div>') +
    '<span class="lm-meta" title="' + esc(line) + '">' + (line ? esc(line) : '&nbsp;') + '</span></div>';
}
function bmBytes(b) { return b == null ? null : (b >= 1e8 ? bmNum(b / 1e9, 1) + ' GB' : bmNum(b / 1e6) + ' MB'); }
// one duration format everywhere: 48 s, 12 min 05 s, 1 h 12 min (no seconds past an hour)
function bmDur(s) {
  s = Math.max(0, Math.floor(Number(s) || 0));
  if (s >= 3600) return Math.floor(s / 3600) + ' h ' + Math.floor((s % 3600) / 60) + ' min';
  if (s >= 60) return Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2, '0') + ' s';
  return s + ' s';
}
let bmSkew = 0, bmClockId = null;                                   // server clock minus page clock, so times are the server's, not the page-load's
function bmNow() { return Date.now() / 1000 + bmSkew; }
function bmSince(start) { return start ? bmDur(bmNow() - start) : ''; }
function bmClockText(s) { return BM_WORDS.run.elapsed + ' ' + bmSince(s.batch_started_epoch_s) + (s.model_started_epoch_s ? ' · ' + BM_WORDS.run.thisModel + ' ' + bmSince(s.model_started_epoch_s) : ''); }
function bmClockTick() {
  const el = document.getElementById('bmClock');
  if (el && bmStatus && bmStatus.running) el.textContent = bmClockText(bmStatus);
  else if (el && bmRemoteOn()) el.textContent = bmClockText({ batch_started_epoch_s: bmRemote.run.batchStartedEpochS, model_started_epoch_s: bmRemote.run.modelStartedEpochS });
  if (document.querySelectorAll) document.querySelectorAll('.bm-live').forEach(e => { e.textContent = ' (' + bmSince(Number(e.getAttribute('data-start'))) + ')'; });
}
function bmClockLoop() { bmClockId = null; bmClockTick(); if (bmStatus && bmStatus.running || bmRemoteOn() || (document.querySelector && document.querySelector('.bm-live'))) bmClockId = setTimeout(bmClockLoop, 1000); }
function bmSec(ms) { return ms == null ? null : (ms < 100 ? bmNum(ms / 1000, 2) : bmNum(ms / 1000, 1)) + ' s'; }
function bmNM(word) { return '<span class="soft">' + esc(word || BM_WORDS.res.notMeasured) + '</span>'; }
function bmVram(first) { return first ? '<span class="bm-tip" title="' + esc(BM_WORDS.vramTip) + '" style="border-bottom:1px dotted var(--muted)">VRAM</span>' : 'VRAM'; }
function bmLevel(dot) { return dot === 'green' ? 'ok' : (dot === 'amber' ? 'warn' : (dot === 'red' ? 'bad' : 'off')); }
function bmDate(epoch) { if (!epoch) return ''; const d = new Date(epoch * 1000); return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }); }
function bmBtn(label, onclick, o) {
  o = o || {};
  return '<button' + (o.primary ? '' : ' class="ghost"') + (o.off ? ' disabled' : '') + (o.title ? ' title="' + esc(o.title) + '"' : '') + ' onclick="' + onclick + '">' + esc(label) + '</button>';
}
function bmWarn(text) { return '<div class="lm-warn" style="margin:8px 0">⚠️ ' + esc(text) + '</div>'; }

// ---- 1. the models ----
function bmPendingOf(rows) {
  const need = (rows || []).filter(r => r.ticked && r.source === 'community' && (r.state === 'not_downloaded' || r.state === 'downloading'));
  return { ids: need.map(r => r.id), count: need.length, bytes: need.reduce((n, r) => n + (r.sizeBytes || 0), 0) };
}
function bmModelsHtml(st) {
  const W = BM_WORDS.models, rows = (st && st.models) || [], need = bmPendingOf(rows), q = (st && st.queue) || {};
  let h = '<div class="bm-hint bm-note">' + esc(W.note) + '</div>';
  const btns = '<div class="bm-right bm-addrow">' + (need.count && !q.active ? bmBtn(W.downloadTicked, 'bmDownloadTicked()') : '') + bmBtn(W.addFile, 'bmAddFile()', { primary: true }) + '</div>';
  if (!rows.length) return h + '<div class="lm-muted">' + esc(W.none) + '</div>' + btns;
  const tickable = rows.filter(r => r.state !== 'missing'), all = tickable.length > 0 && tickable.every(r => r.ticked);
  h += '<div class="bm-wrap"><table class="ov-table bm-table bm-models"><colgroup><col style="width:3rem"><col><col style="width:8rem"><col style="width:10rem"><col style="width:13rem"><col style="width:14rem">' + (bmRemote.dev ? '<col style="width:9rem"><col style="width:14rem">' : '') + '</colgroup>' +
    '<tr><th class="bm-tickcell"><input type="checkbox" title="' + esc(W.selectAll) + '" aria-label="' + esc(W.selectAll) + '" ' + (all ? 'checked ' : '') + 'onchange="bmTickAll(this.checked)"></th><th>' + esc(W.model) + '</th><th>' + esc(W.size) + '</th><th>' + esc(W.from) + '</th><th>' + esc(W.status) + '</th>' + bmRemoteHeadHtml() + '<th></th></tr>';
  for (const r of rows) h += bmModelRow(r);
  h += '</table></div>' + bmRemoteSummaryHtml(rows);
  if (rows.some(r => r.fit === 'tight' || r.fit === 'too_big')) h += '<div class="bm-hint bm-note"><span class="lm-dot" style="margin:0 4px 0 0;cursor:default">●</span>' + esc(W.fitLegend) + '</div>';
  if (bmListMsg) h += '<div class="bm-msg">' + bmWarn(bmListMsg) + '</div>';
  if (need.count) h += '<div class="bm-foot">' + esc(W.needDl(need.count, bmBytes(need.bytes))) + '</div>';
  return h + btns;
}
// "On this card right now" (screen A): the card's total, the biggest users and what is free for the test. Always drawn when the card could be read; a reading that could not be taken is not drawn.
// The strength of each segment of the "On this card right now" bar (the first three programs, then the rest). The row markers read the same list, so a marker is always its segment's colour.
const BM_SEG_OP = [1, 0.7, 0.45, 0.25];
function bmCardHtml() {
  const W = BM_WORDS.card, c = bmCard;
  if (!c || !c.available || !c.totalMb || (bmStatus && bmStatus.running)) return '';          // while a test runs the live view lists the programs instead
  const progs = c.programs || [], shown = progs.slice(0, 3), rest = progs.slice(3), op = BM_SEG_OP;
  const seg = (mb, i) => '<i style="width:' + Math.min(100, Math.max(0.5, 100 * mb / c.totalMb)).toFixed(1) + '%;opacity:' + op[i] + '"></i>';
  const restMb = rest.reduce((a, p) => a + p.mb, 0);
  const mk = i => progs.length ? '<span class="bm-key" style="opacity:' + op[i] + '"></span>' : '';          // the segment's colour; no bar, no markers
  const gap = progs.length ? '<span class="bm-key" style="visibility:hidden"></span>' : '';                   // "Free for the test" is the empty track: no colour, but the text starts in the same place
  let h = '<div class="bm-card"><div class="bm-q">' + esc(W.title) + ' <a class="lm-link" href="#" style="font-weight:400" onclick="bmCardRefresh();return false">' + esc(W.refresh) + '</a></div>';
  if (progs.length) h += '<div class="bm-cbar">' + shown.map((p, i) => seg(p.mb, i)).join('') + (rest.length ? seg(restMb, 3) : '') + '</div>';
  h += '<table class="ov-table bm-table"><colgroup><col><col style="width:7rem"></colgroup>' + shown.map((p, i) => '<tr><td title="' + esc(p.name) + '">' + mk(i) + esc(p.name) + '</td><td class="r">' + esc(bmGb(p.mb)) + '</td></tr>').join('') +
    (rest.length ? '<tr><td class="lm-muted">' + mk(3) + esc(W.more(rest.length)) + '</td><td class="r lm-muted">' + esc(bmGb(restMb)) + '</td></tr>' : '') +
    (c.freeMb != null ? '<tr><td>' + gap + '<b>' + esc(W.free) + '</b></td><td class="r"><b>' + esc(bmGb(c.freeMb)) + '</b></td></tr>' : '') + '</table></div>';
  return h;
}
function bmDrawCard() { const el = document.getElementById('bmCard'); if (el) el.innerHTML = bmCardHtml(); }
async function bmLoadCard(fresh) {
  try { bmCard = await bmGet('/config/benchmarks/card-usage' + (fresh ? '?fresh=1' : '')); } catch (e) { bmCard = null; }
  bmDrawCard();
}
async function bmCardRefresh() { await bmLoadCard(true); bmRefit(); }
// The Local device tab's dot, for the same verdict (computed on the server from the same function): amber tight fit, red too big, nothing when it fits. Nothing is ever blocked by it.
function bmFitDot(r) {
  if (r.fit !== 'tight' && r.fit !== 'too_big') return '';
  const W = BM_WORDS.models, big = r.fit === 'too_big', al = r.fitAlongside !== false;
  const gb = mb => bmNum(mb / 1024, 1), n = r.fitSlots || 8, known = r.fitNeedMb != null && r.fitFreeMb != null;
  let tip;
  if (known && bmCard && bmCard.available && bmCard.freeMb != null) tip = BM_WORDS.models.fitHoverNow(gb(r.fitNeedMb), n, gb(r.fitFreeMb)) + (!big && r.fitConfidence === 'weak' ? ' ' + W.fitRough : '');
  else if (big) tip = known ? W.fitBigNum(n, gb(r.fitNeedMb), gb(r.fitFreeMb)) : (al ? W.fitBig : W.fitBigCard);
  else tip = (al ? W.fitTight : W.fitTightCard) + (known ? ' ' + W.fitTightNum(n, gb(r.fitNeedMb), gb(r.fitFreeMb)) : '') + (r.fitConfidence === 'weak' ? ' ' + W.fitRough : '');
  return '<span class="lm-dot' + (big ? ' bm-fit-big' : '') + '" title="' + esc(tip) + '">●</span>';
}
// the list file can be edited by hand: only an https link on exactly huggingface.co becomes a link
function bmHfUrl(s) {
  try { const u = new URL(String(s || '')); return u.protocol === 'https:' && u.hostname === 'huggingface.co' && !u.username && !u.password ? u.href : ''; } catch (e) { return ''; }
}
// The model facts line, the cache line, the engine line and the VRAM spill line. A part the data does not have is not drawn.
function bmCtxK(c) { return c >= 1024 ? Math.round(c / 1024) + 'k' : String(c); }
function bmSizeMb(mb) { return mb >= 1024 ? bmNum(mb / 1024, 1) + ' GB' : mb + ' MB'; }
function bmFactsText(f) {
  const W = BM_WORDS.facts;
  if (!f) return '';
  return [f.arch, f.params, f.quant, f.layers ? W.layers(f.layers) : null, f.max_ctx ? W.maxCtx(bmCtxK(f.max_ctx)) : null].filter(Boolean).join(' \u00b7 ');
}
function bmCacheText(c) {
  const W = BM_WORDS.facts;
  if (!c || c.a_mb == null || !c.a_ctx) return '';
  const parts = [W.cachePart(bmCtxK(c.a_ctx), bmSizeMb(c.a_mb))];
  if (c.b_mb != null && c.b_ctx) parts.push(W.cachePart(bmCtxK(c.b_ctx), bmSizeMb(c.b_mb)));
  return W.cacheLine(parts);
}
function bmFactsHtml(src, cls, tag) {
  const W = BM_WORDS.facts, f = bmFactsText(src.facts), c = bmCacheText(src.cache), t = tag || 'span';
  return (f ? '<' + t + ' class="' + cls + '" title="' + esc(W.factsTip) + '">' + esc(f) + '</' + t + '>' : '') + (c ? '<' + t + ' class="' + cls + '" title="' + esc(W.cacheTip) + '">' + esc(c) + '</' + t + '>' : '');
}
function bmModelRow(r) {
  const W = BM_WORDS.models, k = jsq(r.key);
  const can = r.state !== 'missing';
  const d = r.download;
  let st = '', a1 = '', a2 = '';
  if (r.state === 'downloaded') { st = ovDot('ok') + esc(W.downloaded); }
  else if (r.state === 'local') { st = ovDot('ok') + esc(W.onThisPc); }
  else if (r.state === 'missing') { st = ovDot('bad') + esc(W.missing); }
  else if (d && d.state === 'running') { st = bmDlBox('warn', W.downloading + ' ' + d.percent + '%', d.percent, bmDlLine(d)); }
  else if (d && d.state === 'paused') { st = bmDlBox('off', W.paused + ' ' + d.percent + '%', d.percent, bmDlLine(d)); }
  else if (r.ticked && !(d && d.error)) { st = bmDlBox('off', W.willDownload, null, ''); }
  else { st = ovDot('off') + esc(r.ticked ? W.willDownload : W.notDownloaded) + (d && d.error ? '<span class="lm-meta lm-bad" title="' + esc(d.error) + '">' + esc(d.error) + '</span>' : ''); }
  if (r.failure && r.state !== 'downloaded') st += '<span class="lm-meta lm-bad" title="' + esc(r.failure) + '">' + esc(r.failure) + '</span>';
  if (r.source === 'community') {
    if (r.state === 'not_downloaded' && !(d && d.state === 'paused')) a1 = bmBtn(W.download, 'bmDownload(\'' + jsq(r.id) + '\')', { primary: true });
    if (d && d.state === 'running') a1 = bmBtn(W.pause, 'lmPause(\'model:' + jsq(r.id) + '\');bmSoon()');
    if (d && d.state === 'paused') a1 = bmBtn(W.resume, 'bmResume(\'' + jsq(r.id) + '\')', { primary: true });
    if (d && (d.state === 'running' || d.state === 'paused')) a2 = bmBtn(W.cancel, 'bmCancelDl(\'' + jsq(r.id) + '\')');
    else a2 = bmBtn(W.remove, 'bmRemove(\'' + k + '\')');
  } else if (!r.fromLocalTab) {
    a2 = bmBtn(W.remove, 'bmRemove(\'' + k + '\')');
  }
  const from = r.source === 'community' ? (r.publisher || '') : W.local;
  const title = r.title + (r.quantisation ? ' ' + r.quantisation : '');
  const url = r.source === 'community' ? bmHfUrl(r.link) : '';
  const rfit = bmRemoteFitDot(r);
  const nameHtml = (url ? '<a class="lm-link bm-hflink" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(title) + '</a>' : esc(title)) + (rfit == null ? bmFitDot(r) : rfit);
  return '<tr><td class="bm-tickcell"><input type="checkbox" ' + (r.ticked ? 'checked ' : '') + (can ? '' : 'disabled ') + 'onchange="bmTick(\'' + k + '\', this.checked)"></td>' +
    '<td title="' + esc(title) + '"><b>' + nameHtml + '</b>' + bmFactsHtml(r, 'bm-small') + '</td><td>' + (r.sizeBytes ? esc(bmBytes(r.sizeBytes)) : bmNM('')) + '</td><td>' + esc(from) + '</td>' +
    '<td class="bm-st bm-wrapok">' + st + '</td>' + bmRemoteCellsHtml(r) + '<td style="overflow:visible"><div class="bm-slots"><div class="bm-slot">' + a1 + '</div><div class="bm-slot">' + a2 + '</div></div></td></tr>';
}

// ---- 4. while it runs ----
// The words for the step the number names. While the engine loads or restarts inside that step, say so after the step's own words, with the seconds spent so far (the engine gives no
// load progress of its own, so the seconds are the sign of life). `s.engine_busy` is `{state: 'loading' | 'restarting', slots, seconds}`.
function bmStepWords(s) {
  const W = BM_WORDS.run, e = s.engine_busy || null, base = bmStepName(s), sec = e && e.seconds != null ? ' (' + e.seconds + ' s)' : '';
  if (e && e.state === 'restarting' && (s.step || '') !== 'engine_restart') return base + ' · ' + W.restart(e.slots) + sec;
  if (e && e.state === 'loading' && (s.step || '') === 'load') return base + sec;
  return base;
}
function bmStepName(s) {
  const W = BM_WORDS.run, step = s.step || '';
  if (step === 'hashing') return W.hashing(s.hashing ? s.hashing.percent : null);
  if (step === 'load') return W.load;
  if (step === 'warmup') return W.warmup;
  if (step.indexOf('level:') === 0) return W.level(parseInt(step.slice(6), 10));
  if (step === 'engine_restart') return W.restart('');
  if (step === 'sustained') return W.sustained;
  if (step === 'unload') return W.unload;
  if (step === 'cooldown') return W.cooldown;
  if (step === 'finished') return W.finished;
  return W.starting;
}
function bmTile(k, v, s) {
  return '<div class="bm-tile"><div class="bm-k">' + k + '</div><div class="bm-v">' + (v == null ? bmNM(BM_WORDS.run.notMeasured) : esc(v)) + '</div><div class="bm-s">' + (s ? esc(s) : '&nbsp;') + '</div></div>';
}
function bmTilesHtml(rd) {
  const W = BM_WORDS.run, gpus = (rd && rd.gpus) || [], peaks = (rd && rd.peaks) || {}, mach = (rd && rd.machine) || null;
  let h = '<div class="bm-tiles">';
  gpus.forEach((g, i) => {
    const p = peaks[String(g.gpu_index)] || {}, hi = f => (p[f] || {}).highest_sampled;
    const many = gpus.length > 1;
    if (many) h += '<div class="bm-hint" style="grid-column:1/-1">' + esc(BM_WORDS.det.gpu(g.gpu_index) + (g.name ? ': ' + g.name : '')) + '</div>';
    h +=
      bmTile(bmVram(i === 0), g.vram_used_mb == null ? null : bmGb(g.vram_used_mb).replace(' GB', '') + (g.vram_total_mb ? ' of ' + bmGb(g.vram_total_mb) : ' GB'), hi('vram_used_mb') != null ? W.highestSoFar + ' ' + bmGb(hi('vram_used_mb')) : '') +
      bmTile(esc(W.util), g.gpu_util_pct == null ? null : bmNum(g.gpu_util_pct) + '%', '') +
      bmTile(esc(W.temp), g.temp_c == null ? null : bmNum(g.temp_c) + ' °C', hi('temp_c') != null ? W.highestSoFar + ' ' + bmNum(hi('temp_c')) + ' °C' : '') +
      bmTile(esc(W.power), g.power_w == null ? null : bmNum(g.power_w) + ' W', g.power_limit_w != null ? 'of ' + bmNum(g.power_limit_w) + ' W' : '') +
      bmTile(esc(W.clock), g.clock_mhz == null ? null : bmNum(g.clock_mhz) + ' MHz', hi('clock_mhz') != null ? W.highest + ' ' + bmNum(hi('clock_mhz')) + ' MHz' : '');
  });
  if (!gpus.length) h += bmTile(bmVram(true), null, '') + bmTile(esc(W.util), null, '') + bmTile(esc(W.temp), null, '') + bmTile(esc(W.power), null, '') + bmTile(esc(W.clock), null, '');
  h += bmTile(esc(W.cpu), mach && mach.cpu_util_pct != null ? bmNum(mach.cpu_util_pct) + '%' : null, '') +
    bmTile(esc(W.ram), mach && mach.ram_used_mb != null ? bmGb(mach.ram_used_mb).replace(' GB', '') + (mach.ram_total_mb ? ' of ' + bmGb(mach.ram_total_mb) : ' GB') : null, '') + '</div>';
  return h + '</div>';
}
// The live header's two short columns. Each line only when its data exists; a column with no line is not drawn.
function bmSpecLines(mf) {
  const W = BM_WORDS.facts, f = (mf && mf.facts) || {}, c = (mf && mf.cache) || null, out = [];
  const arch = [f.arch, f.params, f.quant].filter(Boolean);
  if (arch.length) out.push({ text: W.archLabel + ': ' + arch.join(' · '), tip: W.factsTip });
  const size = [f.layers ? W.layers(f.layers) : null, f.max_ctx ? W.maxCtx(bmCtxK(f.max_ctx)) : null].filter(Boolean);
  if (size.length) out.push({ text: W.sizeLabel + ': ' + size.join(' · '), tip: W.factsTip });
  if (c && c.a_mb != null && c.a_ctx) {
    const parts = [bmCtxK(c.a_ctx) + ' = ' + bmSizeMb(c.a_mb)];
    if (c.b_mb != null && c.b_ctx) parts.push(bmCtxK(c.b_ctx) + ' = ' + bmSizeMb(c.b_mb));
    out.push({ text: W.cacheEstLabel + ': ' + parts.join(' | '), tip: W.cacheTip });
  }
  return out;
}
function bmStatusHtml(s) {
  const W = BM_WORDS.facts, e = s.engine || null;
  let h = '';
  if (e && (e.name || e.build || e.backend)) h += '<div class="bm-small" title="' + esc(W.engineTip) + '">' + esc(W.engine(e.name, e.build, e.backend)) + '</div>';
  if (e && e.slots != null && e.busy != null) h += '<div class="bm-small" title="' + esc(W.engineTip) + '">' + esc(W.queue(e.busy, e.slots)) + '</div>';
  if (s.paged_mb > 0) h += '<div class="lm-warn bm-small" title="' + esc(W.spillTip) + '">⚠️ ' + esc(W.spill(bmSizeMb(s.paged_mb))) + '</div>';
  return h;
}
function bmColumnsHtml(s) {
  const W = BM_WORDS.facts, specs = bmSpecLines(s.model_facts), status = bmStatusHtml(s);
  if (!specs.length && !status) return '';
  const col = (head, body) => '<div><div class="bm-colhead">' + esc(head) + '</div>' + body + '</div>';
  return '<div class="bm-cols">' + (specs.length ? col(W.specsHead, specs.map(l => '<div class="bm-small" title="' + esc(l.tip) + '">' + esc(l.text) + '</div>').join('')) : '') +
    (status ? col(W.statusHead, status) : '') + '</div>';
}
function bmRunHtml(s) {
  if (!s || !s.running) return s && s.batch_stopped && s.message ? '<div class="card">' + bmWarn(s.message) + '</div>' : '';
  const W = BM_WORDS.run, total = s.steps_total || 0, done = Math.min(s.steps_done || 0, total);
  const pct = total ? Math.round(100 * done / total) : 0;
  return '<div class="card"><div class="bm-head"><div class="bm-h" style="margin:0"><b>' + esc(W.model(s.model_index || 1, s.models_total || 1, s.model_name || '')) + '</b></div>' +
    '<div>' + bmBtn(BM_WORDS.setup.stop, 'bmStop()', { primary: true }) + '</div></div>' +
    '<div class="bm-steprow"><div class="bm-step lm-muted" id="bmStepLine">' + esc(total ? W.step(Math.min(done + 1, total), total, bmStepWords(s)) : bmStepWords(s)) + '</div>' +
    (s.batch_started_epoch_s ? '<div class="bm-step lm-muted" id="bmClock">' + esc(bmClockText(s)) + '</div>' : '') + '</div>' +
    '<div class="bm-bar"><i style="width:' + pct + '%"></i></div>' + bmColumnsHtml(s) +
    (s.waiting_memory ? '<div class="bm-step lm-muted" id="bmWaitMem">' + esc(W.waitingMemory) + '</div>' : '') +
    (s.layers && s.layers.on_card != null && s.layers.total != null && s.layers.on_card < s.layers.total ? '<div class="bm-step lm-muted" id="bmPartCpu">' + esc(W.partCpu) + '</div>' : '') +
    (s.last_stop && s.last_stop.index < (s.model_index || 1) ? '<div class="bm-step lm-muted" id="bmStopped">' + esc(W.stoppedLive) + '</div>' : '') +
    '<div title="' + esc(W.readings) + '">' + bmTilesHtml(s.readings) + '</div>' + bmLiveMemHtml(s) + '</div>';
}
// The card's memory the live readings give (the first card, the one the VRAM tile shows): `{total, used}` in MB, either may be null.
// Screen C: what the engine itself said it took (measured), shown against the whole card, and the biggest other programs on the card (read every 5 seconds by the runner). A number that is not there is not drawn.
function bmCardMem(s) {
  const g = ((s.readings && s.readings.gpus) || [])[0];
  return { total: g && g.vram_total_mb ? g.vram_total_mb : null, used: g && g.vram_used_mb != null ? g.vram_used_mb : null };
}
// The whole-card bar: the model, the cache, working space, everything else, then free (the empty track). A part with no measured figure is left out; with no card total the bar is the
// three measured parts alone, as before.
function bmCardBar(s, m, W) {
  const cm = bmCardMem(s), engine = m.model + m.cache + m.compute;
  const parts = [{ mb: m.model, name: W.modelTitle, key: 'bm-k1' }, { mb: m.cache, name: W.cacheAt(m.slots || ''), key: 'bm-k2' }, { mb: m.compute, name: W.workSpace, key: 'bm-k3' }];
  if (cm.total && cm.used != null) parts.push({ mb: Math.max(0, cm.used - engine), name: W.elseTitle, key: 'bm-k4' });
  const base = cm.total && cm.total >= parts.reduce((a, p) => a + p.mb, 0) ? cm.total : parts.reduce((a, p) => a + p.mb, 0);
  return '<div class="bm-cbar">' + parts.filter(p => p.mb > 0).map(p => '<i class="' + p.key + '" style="width:' + (100 * p.mb / base).toFixed(1) + '%" title="' + esc(p.name + ' · ' + bmGb(p.mb)) + '"></i>').join('') + '</div>';
}
function bmLiveMemHtml(s) {
  const W = BM_WORDS.run, m = s.memory_model, users = (s.card_users || []).slice(0, 3);
  const haveM = m && m.model != null && m.cache != null && m.compute != null;
  if (!haveM && !users.length) return '';
  const key = k => '<span class="bm-key ' + k + '"></span>';
  let h = '<div class="bm-live2">';
  if (haveM) {
    h += '<div><div class="lm-muted">' + esc(W.modelTitle) + ' <span class="soft">' + esc(W.measuredWord) + '</span></div>' + bmCardBar(s, m, W) +
      '<table class="ov-table bm-table"><colgroup><col><col style="width:7rem"></colgroup><tr><td title="' + esc(s.model_name || '') + '">' + key('bm-k1') + esc(s.model_name || '') + '</td><td class="r">' + esc(bmGb(m.model)) + '</td></tr><tr><td>' + key('bm-k2') + esc(W.cacheAt(m.slots || '')) + '</td><td class="r">' + esc(bmGb(m.cache)) +
      '</td></tr><tr><td>' + key('bm-k3') + esc(W.workSpace) + '</td><td class="r">' + esc(bmGb(m.compute)) + '</td></tr></table></div>';
  }
  if (users.length) {
    h += '<div><div class="lm-muted">' + key('bm-k4') + esc(W.elseTitle) + '</div>' + (haveM ? '<div class="bm-cbar" style="visibility:hidden"></div>' : '') + '<table class="ov-table bm-table"><colgroup><col><col style="width:7rem"></colgroup>' +
      users.map(p => '<tr><td title="' + esc(p.name) + '">' + esc(p.name) + '</td><td class="r">' + esc(bmGb(p.mb)) + '</td></tr>').join('') + '</table></div>';
  }
  return h + '</div>';
}

// ---- 5. the results ----
function bmStat(s, fmt, nm) {
  if (!s) return nm ? '<span class="soft">' + esc(nm) + '</span>' : bmNM('');
  return '<span class="bm-big">' + esc(fmt(s.mean)) + '</span><span class="bm-small">' + esc(fmt(s.min) + ' – ' + fmt(s.max)) + '</span>';
}
// a run's own time: from its recorded start to its recorded end; a run still going counts up; no end recorded means no time (never a guess)
function bmRunSecs(r) { return r.started_epoch_s && r.ended_epoch_s ? r.ended_epoch_s - r.started_epoch_s : null; }
function bmRunTime(r) {
  if (r.status === 'running' && r.started_epoch_s) return '<span class="bm-live" data-start="' + Number(r.started_epoch_s) + '"> (' + esc(bmSince(r.started_epoch_s)) + ')</span>';
  const t = bmRunSecs(r);
  return t == null || r.status === 'newer_version' ? '' : ' (' + esc(bmDur(t)) + ')';
}
function bmRunStatus(r) {
  const W = BM_WORDS.res;
  const why = r.reason ? (W.reason[r.reason] || r.reason) : '';
  if (r.status === 'newer_version') return { dot: 'off', word: W.newer, tip: '', time: '', obs: '', partial: false };
  if (r.spill === 'detected') return { dot: 'warn', word: W.spill, tip: W.spillTip + (r.spill_evidence ? ' ' + r.spill_evidence : ''), time: bmRunTime(r), obs: '', partial: !!r.partial };
  const obs = r.spill === 'unknown' && r.spill_observation ? r.spill_observation : '';
  const sp = r.stop || {};
  let sub = '';
  if (r.status === 'stopped_for_memory') sub = sp.concurrency ? W.stoppedMemoryAt(sp.concurrency) : W.stoppedMemoryEarly;
  if (r.status === 'stopped_too_long') sub = W.stoppedTimeAt(Math.round((sp.limit_s || 0) / 60));
  const m = { completed: ['ok', W.completed], running: ['warn', W.running], failed: ['bad', W.failed + (why ? ' — ' + why : '')],
    stopped_for_memory: ['warn', W.stoppedMemory], stopped_too_long: ['warn', W.stoppedTime],
    interrupted: ['warn', W.interrupted + ' — ' + W.stepsDone(r.steps_done || 0, r.steps_total || 0)], cancelled: ['off', W.cancelled],
    invalidated: ['warn', W.invalidated + (why ? ' — ' + why : '')] }[r.status] || ['off', String(r.status || '')];
  return { dot: m[0], word: m[1], tip: why || obs, obs: sub || obs, time: bmRunTime(r), partial: !!r.partial };
}
function bmSustained(s) {
  const W = BM_WORDS.res;
  if (!s || !s.status) return bmNM('');
  const pct = typeof s.lowest_pct === 'number' ? s.lowest_pct : null;
  const tip = typeof s.baseline_tok_s === 'number' && typeof s.lowest_tok_s === 'number' ? ' title="' + esc(W.sustTip(bmNum(s.baseline_tok_s), bmNum(s.lowest_tok_s))) + '"' : '';
  const two = (main, sub) => '<span class="bm-big"' + tip + '>' + esc(main) + '</span>' + (sub ? '<span class="bm-small">' + esc(sub) + '</span>' : '');
  if (s.status === 'completed') return two(W.sustCompleted, pct == null ? '' : W.sustLowHeld(pct));
  if (s.status === 'slowdown_detected') return two(W.sustSlow(s.after_minutes), pct == null ? '' : W.sustLowSlow(pct));
  if (s.status === 'failed' || s.status === 'interrupted') return two(s.status === 'failed' ? W.sustFailed : W.sustInterrupted, s.reason ? (W.reason[s.reason] || s.reason) : '');
  if (s.status === 'not_run') return esc(W.sustNotRun);
  return esc(s.status);
}
function bmHeadroomCell(h) {
  if (!h || h.mb == null) return ovDot('off') + '<span class="soft" title="' + esc(BM_WORDS.res.headroomTip) + '">' + esc(BM_WORDS.res.notEnough) + '</span>';
  const gb = Math.abs(h.mb) < 1 ? 0 : h.mb / 1024;
  return ovDot(bmLevel(h.dot)) + '<span title="' + esc(BM_WORDS.res.headroomTip) + '">' + esc((gb < 0 ? '-' : '') + bmNum(Math.abs(gb), 1) + ' GB') + '</span>';
}
// The note (measured, and our guess from before the run) on the line above the three numbers the engine itself printed. Our guess from before the run. Either may be missing; nothing is drawn for a missing one.
function bmBreakdownCell(mb) {
  if (!mb) return bmNM('');
  const g = v => bmNum(v / 1024, 1), m = mb.measured, W = BM_WORDS.res;
  const note = [m ? BM_WORDS.run.measuredWord : '', mb.estimated_mb ? W.estBefore(g(mb.estimated_mb) + ' GB') : ''].filter(Boolean).join(' · ');
  return (note ? '<span class="bm-small">' + esc(note) + '</span>' : '') + (m ? esc(g(m.model) + ' + ' + g(m.cache) + ' + ' + g(m.compute) + ' GB') : bmNM(''));
}
function bmResultRow(r, ticked, hasChecks) {
  const W = BM_WORDS.res, st = bmRunStatus(r), id = jsq(r.run_id);
  if (r.status === 'newer_version') return '<tr><td></td><td colspan="13">' + ovDot('off') + esc(W.newer) + '</td></tr>';
  const av = r.availability || {};
  const gen = r.generation_tok_s ? bmStat(r.generation_tok_s, v => bmNum(v)) : (av.generation_tok_s === 'not_reported' ? '<span class="soft">' + esc(W.notReported) + '</span>' : bmNM(''));
  const pr = r.prompt_tok_s ? bmStat(r.prompt_tok_s, v => bmNum(v)) : (av.prompt_tok_s === 'not_reported' ? '<span class="soft">' + esc(W.notReported) + '</span>' : bmNM(''));
  let tt = r.ttft_ms ? bmStat(r.ttft_ms, v => bmSec(v)) : (av.ttft_ms === 'not_reported' ? '<span class="soft">' + esc(W.notReported) + '</span>' : bmNM(''));
  if (r.ttft_ms && typeof r.ttft_ms.p95 === 'number') tt += '<span class="bm-small">' + esc(W.slowest(bmSec(r.ttft_ms.p95), r.ttft_ms.n)) + '</span>';
  const prof = r.profile === 'custom' ? ' <span class="bm-custom">' + esc(W.custom) + '</span>' : '';
  return '<tr><td class="bm-tickcell"><input type="checkbox" ' + (ticked ? 'checked ' : '') + 'onchange="bmResTick(\'' + id + '\', this.checked)"></td>' +
    '<td class="bm-nm" title="' + esc(r.model) + '"><button class="bm-name" onclick="bmOpen(\'' + id + '\')">' + esc(String(r.model).replace(/\.gguf$/i, '')) + '</button></td>' +
    '<td>' + (r.size_bytes ? esc(bmBytes(r.size_bytes)) : bmNM('')) + '</td><td>' + (r.peak_vram_mb != null ? esc(bmGb(r.peak_vram_mb)) : bmNM('')) + '</td>' +
    '<td>' + bmBreakdownCell(r.memory_breakdown) + '</td><td class="bm-st">' + bmHeadroomCell(r.headroom) + '</td><td>' + (r.load_time_s != null ? esc(bmNum(r.load_time_s, r.load_time_s < 10 ? 1 : 0) + ' s') : bmNM('')) + '</td>' +
    '<td class="r">' + pr + '</td><td class="r">' + gen + '</td><td class="r">' + tt + '</td><td>' + (r.peak_temp_c != null ? esc(bmNum(r.peak_temp_c) + ' °C') : bmNM('')) + '</td>' +
    '<td class="bm-wrapok bm-tip" title="' + esc(W.sustainedTip) + '">' + bmSustained(r.sustained) + '</td>' + (hasChecks ? '<td>' + (r.checks ? esc(r.checks.passed + ' ' + W.of(r.checks.total)) : bmNM('')) + '</td>' : '') +
    '<td class="bm-st bm-wrapok" title="' + esc(st.tip || '') + '">' + ovDot(st.dot) + esc(st.word) + st.time + (st.obs ? '<span class="bm-small">' + esc(st.obs) + '</span>' : '') + (st.partial ? '<span class="bm-small">' + esc(W.partial) + '</span>' : '') + prof + bmResultFlags(r) + '</td></tr>';
}
// the batch total: first start to last end, only when several models of one batch are shown and every one recorded its end
function bmTotalHtml(rows) {
  const r = (rows || []).filter(x => x.status !== 'newer_version');
  if (r.length < 2 || r.some(x => !x.started_epoch_s || !x.ended_epoch_s)) return '';
  const secs = Math.max.apply(null, r.map(x => x.ended_epoch_s)) - Math.min.apply(null, r.map(x => x.started_epoch_s));
  return '<div class="bm-legend"><span>' + esc(BM_WORDS.res.total + ' ' + bmDur(secs)) + '</span></div>';
}
function bmResultsHtml(rows, ticks) {
  const W = BM_WORDS.res;
  if (!rows || !rows.length) return '<h3 class="bm-h">' + esc(W.title) + '</h3><div class="lm-muted">' + esc(W.none) + '</div>';
  const level = Math.max.apply(null, rows.map(r => r.level || 0).concat([0])) || 8;
  const hasChecks = rows.some(r => r.checks);
  const legend = '<div class="bm-legend"><span>' + esc(W.legendLabel) + '</span>' + W.legend.map(l => '<span class="bm-legend-item">' + ovDot(l[0]) + esc(l[1]) + '</span>').join('') + '</div>';
  const dev = bmResultsDevice(rows);
  let h = '<div class="bm-head"><h3 class="bm-h">' + esc(W.title) + (dev ? ' <span class="lm-muted" style="font-weight:400">' + esc(BM_WORDS.remote.resultsFrom(dev)) + '</span>' : '') + '</h3>' + bmTotalHtml(rows) + legend + '</div>' + bmResultsServicesHtml(rows, dev) + '<div class="bm-wrap"><table class="ov-table bm-table bm-res">' + '<tr><th></th><th>' + esc(W.model) + '</th><th>' + esc(W.size) + '</th>' +
    '<th class="bm-tip" title="' + esc(W.peakTip(level)) + '">' + esc(W.peakVram) + '</th><th>' + esc(W.memBreakdown) + '</th><th class="bm-tip" title="' + esc(W.headroomTip) + '">' + esc(W.headroom) + '</th><th>' + esc(W.load) + '</th>' +
    '<th class="r">' + esc(W.prompt) + '</th><th class="r">' + esc(W.gen) + '</th><th class="r">' + esc(W.ttft(level)) + '</th><th>' + esc(W.temp) + '</th><th class="bm-tip" title="' + esc(W.sustainedTip) + '">' + esc(W.sustained) + '</th>' + (hasChecks ? '<th>' + esc(W.checks) + '</th>' : '') + '<th>' + esc(W.status) + '</th></tr>';
  for (const r of rows) h += bmResultRow(r, !!(ticks || {})[r.run_id], hasChecks);
  const stops = rows.filter(r => r.partial).length;
  h += '</table></div>' + (stops ? '<div class="bm-hint bm-note">' + esc(W.stopsSummary(stops)) + '</div>' : '') + '<div class="bm-msg" id="bmResMsg">' + (bmMsg ? '<span class="lm-muted">' + esc(bmMsg) + '</span>' : '') + '</div>';
  const none = !Object.values(ticks || {}).some(v => v);
  h += '<div class="bm-right">' + bmBtn(W.testAgain, 'bmTestAgain()', { primary: true, off: none, title: none ? W.tickFirst : '' }) + bmBtn(W.add, 'bmAddToMp()', { off: none, title: none ? W.tickFirst : '' }) +
    bmBtn(W.view, 'bmViewFirst()', { off: none, title: none ? W.tickFirst : '' }) + bmBtn(BM_WORDS.past.del, 'bmDeleteTicked()', { off: none || !!(bmStatus && bmStatus.running), title: none ? W.tickFirst : '' }) + bmBtn(W.exportAll, 'bmExportAll()') + '</div>';
  return h;
}
function bmPastHtml(rows) {
  const W = BM_WORDS.past;
  if (!rows || !rows.length) return '';
  const shownRows = rows.filter(r => r.status !== 'newer_version');
  const n = shownRows.filter(r => bmPastTicks[r.run_id]).length, running = !!(bmStatus && bmStatus.running);
  const act = (label, fn) => '<button class="bm-textact" onclick="' + fn + '">' + esc(label) + '</button>';
  let h = '<div class="bm-head"><h3 class="bm-h">' + esc(W.title) + '</h3><div class="bm-row" style="margin:0;margin-left:auto">' + act(W.selectAll, 'bmPastSelect(\'all\')') + act(W.invert, 'bmPastSelect(\'invert\')') + act(W.clear, 'bmPastSelect(\'clear\')') +
    bmBtn(n ? W.delN(n) : W.del, 'bmPastDeleteTicked()', { off: !n || running }) + '</div></div>' +
    '<div class="bm-wrap"><table class="ov-table bm-table"><colgroup><col style="width:2rem"><col style="width:8rem"><col><col style="width:8rem"><col style="width:16rem"><col style="width:11rem"></colgroup>';
  for (const r of shownRows) {
    const st = bmRunStatus(r), id = jsq(r.run_id);
    h += '<tr><td class="bm-tickcell"><input type="checkbox" ' + (bmPastTicks[r.run_id] ? 'checked ' : '') + 'onchange="bmPastTick(\'' + id + '\', this.checked)"></td><td>' + esc(bmDate(r.started_epoch_s)) + '</td><td title="' + esc(r.model) + '"><b>' + esc(String(r.model).replace(/\.gguf$/i, '')) + '</b></td><td>' + esc(r.device && r.device.kind === 'courier' ? (r.device.name || '') : BM_WORDS.remote.thisPc) + '</td><td class="bm-st">' + ovDot(st.dot) + esc(st.word) + st.time + '</td>' +
      '<td style="text-align:right;overflow:visible"><div class="bm-row" style="justify-content:flex-end;margin:0">' + bmBtn(W.open, 'bmOpen(\'' + id + '\')') + bmBtn(W.del, 'bmDelete(\'' + id + '\', \'' + jsq(String(r.model).replace(/\.gguf$/i, '')) + '\')') + '</div></td></tr>';
  }
  return h + '</table></div>';
}

// ---- 5c / 8c / 8e: one run ----
function bmRange(s, f) { return s ? f(s.min) + ' – ' + f(s.max) : null; }
function bmKV(rows) {
  let h = '<table class="ov-table bm-table"><colgroup><col style="width:22rem"><col></colgroup><tr><th>' + esc(BM_WORDS.det.reading) + '</th><th class="r">' + esc(BM_WORDS.det.value) + '</th></tr>';
  for (const r of rows) h += '<tr><td>' + esc(r[0]) + '</td><td class="r" title="' + esc(r[1] == null ? BM_WORDS.res.notMeasured : r[1]) + '">' + (r[1] == null ? bmNM('') : esc(r[1])) + '</td></tr>';
  return h + '</table>';
}
function bmChart(title, big, sub, pts, ymin) {
  if (!pts || pts.length < 2) return '<div class="bm-chart"><div class="lm-muted">' + esc(title) + '</div><b>' + bmNM('') + '</b></div>';
  const ys = pts.map(p => p[1]), lo = ymin != null ? ymin : Math.min.apply(null, ys), hi = Math.max.apply(null, ys), span = (hi - lo) || 1;
  const xs = pts.length - 1;
  const pl = pts.map((p, i) => (300 * i / xs).toFixed(1) + ',' + (34 - 30 * (p[1] - lo) / span).toFixed(1)).join(' ');
  return '<div class="bm-chart"><div class="lm-muted">' + esc(title) + '</div><b>' + esc(big) + '</b><svg viewBox="0 0 300 38" preserveAspectRatio="none"><polyline points="' + pl + '"/></svg><div class="lm-muted">' + esc(sub) + '</div></div>';
}
function bmDetailHtml(d) {
  const W = BM_WORDS.det, H = d.header || {}, row = d.row || {};
  if (d.status === 'newer_version') return '<div class="bm-right" style="justify-content:flex-start">' + bmBtn(W.back, 'bmBack()') + '</div><p>' + esc(BM_WORDS.res.newer) + '</p>';
  const gpu = (H.gpus || []).map(g => g.name + (g.vram_total_mb ? ' ' + bmGb(g.vram_total_mb) : '')).join(', ');
  const facts = [String(H.model_file || row.model || '').replace(/\.gguf$/i, ''), H.size_bytes ? bmBytes(H.size_bytes) : null, H.model_id_short ? W.fingerprint + ' ' + H.model_id_short : null,
    H.setup_id ? W.setup + ' S-' + H.setup_id + ' (' + (H.profile === 'custom' ? BM_WORDS.res.custom : 'Standard') + ')' : null, H.run_id ? W.run + ' R-' + String(H.run_id).slice(-4).toUpperCase() : null,
    H.test_set_version ? W.testSet + ' ' + H.test_set_version : null, H.engine ? W.engine + ' ' + H.engine + (H.engine_build ? ' ' + H.engine_build : '') : null,
    gpu ? gpu + (H.driver ? ', ' + W.driver + ' ' + H.driver : '') : null, H.device && H.device.kind === 'courier' ? BM_WORDS.remote.devFact(H.device.name, (H.gpus || [])[0] && H.gpus[0].name, H.driver) : null,
    row.skyrim_state === 'running' ? BM_WORDS.remote.skyrimRunning : null, row.started_epoch_s ? bmDate(row.started_epoch_s) : null, W.ended + ': ' + bmRunStatus(row).word, bmRunSecs(row) != null ? W.testTime + ': ' + bmDur(bmRunSecs(row)) : null].filter(Boolean);
  const id = jsq(H.run_id);
  let h = '<div class="bm-head"><div>' + bmBtn(W.back, 'bmBack()') + '</div><div class="bm-right" style="margin:0">' + bmBtn(W.again, 'bmRepeat(\'' + id + '\')', { primary: true }) + '</div></div>' +
    '<h3 class="bm-h" style="margin-top:12px">' + esc(String(H.model_file || row.model || '').replace(/\.gguf$/i, '')) + '</h3><div class="bm-facts">' + esc(facts.slice(1).join(' · ')) + '</div>';
  const st = bmRunStatus(row);
  if (st.dot === 'warn' && row.spill === 'detected') h += bmWarn(st.word + '. ' + BM_WORDS.res.spillTip);
  else if (row.spill === 'unknown' && row.spill_observation) h += '<div class="lm-muted" style="margin:8px 0">' + esc(row.spill_observation) + '</div>';
  if (d.heat_stop_reading) h += bmWarn(W.heatStopped);
  if (d.settings && d.settings.layers_on_card != null && d.settings.layers_total != null && d.settings.layers_on_card < d.settings.layers_total) h += '<div class="lm-muted" style="margin:6px 0">' + esc(BM_WORDS.run.partCpu) + '</div>';
  const basis = d.thermal_basis ? Object.values(d.thermal_basis) : [];
  if (basis.length && basis.every(b => b === 'none')) h += '<div class="lm-muted" style="margin:8px 0">' + esc(W.noHeatLimit) + '</div>';
  // concurrency
  h += '<h4 class="bm-h2">' + esc(W.concurrency) + '</h4><div class="bm-wrap"><table class="ov-table bm-table bm-res"><tr><th>' + esc(W.atOnce) + '</th><th class="r">' + esc(W.requests) + '</th><th class="r">' + esc(W.ttftAvg) + '</th><th class="r">' + esc(W.ttftP) +
    '</th><th class="r">' + esc(W.ttftRange) + '</th><th class="r">' + esc(W.genAvg) + '</th><th class="r">' + esc(W.genRange) + '</th><th class="r">' + esc(W.promptAvg) + '</th><th class="r">' + esc(W.together) + '</th><th class="r">' + esc(W.failed) + '</th></tr>';
  const cell = v => v == null ? bmNM('') : esc(v);
  for (const l of d.levels || []) {
    const t = l.ttft_ms, g = l.generation_tok_s, p = l.prompt_tok_s;
    h += '<tr><td>' + esc(l.level) + '</td><td class="r">' + cell(l.requests) + '</td><td class="r">' + cell(t ? bmSec(t.mean) : null) + '</td><td class="r">' + cell(t ? bmSec(t.p50).replace(' s', '') + (typeof t.p95 === 'number' ? ' / ' + bmSec(t.p95) : '') : null) +
      '</td><td class="r">' + cell(bmRange(t, bmSec)) + '</td><td class="r">' + cell(g ? bmNum(g.mean) : null) + '</td><td class="r">' + cell(bmRange(g, v => bmNum(v))) + '</td><td class="r">' + cell(p ? bmNum(p.mean) : null) +
      '</td><td class="r">' + cell(l.together_tok_s != null ? bmNum(l.together_tok_s) : null) + '</td><td class="r">' + cell((l.failed || 0) + (l.timed_out || 0)) + '</td></tr>';
  }
  h += '</table></div><div class="bm-foot">' + esc(W.pNote) + '</div>';
  // prompt length
  if ((d.prompt_length || []).length) {
    h += '<h4 class="bm-h2">' + esc(W.promptLength(d.level)) + '</h4><div class="bm-wrap"><table class="ov-table bm-table bm-res"><tr><th>' + esc(W.prompt) + '</th><th class="r">' + esc(W.tokensIn) + '</th><th class="r">' + esc(W.promptSpeed) +
      '</th><th class="r">' + esc(W.readTime) + '</th><th class="r">' + esc(W.ttft) + '</th><th class="r">' + esc(W.gen) + '</th><th class="r">' + esc(W.tokensOut) + '</th></tr>';
    for (const p of d.prompt_length) h += '<tr><td>' + esc(BM_WORDS.setup[p.prompt] || p.prompt) + '</td><td class="r">' + cell(p.tokens_in != null ? bmNum(p.tokens_in) : null) + '</td><td class="r">' + cell(p.prompt_tok_s != null ? bmNum(p.prompt_tok_s) : null) +
      '</td><td class="r">' + cell(p.time_to_read_s != null ? bmNum(p.time_to_read_s, 2) + ' s' : null) + '</td><td class="r">' + cell(p.ttft_ms != null ? bmSec(p.ttft_ms) : null) + '</td><td class="r">' + cell(p.generation_tok_s != null ? bmNum(p.generation_tok_s) : null) +
      '</td><td class="r">' + cell(p.tokens_out != null ? bmNum(p.tokens_out) : null) + '</td></tr>';
    h += '</table></div>';
  }
  // model and settings
  const S = d.settings || {}, mem = d.memory || {};
  const firstGpu = Object.keys(mem.gpus || {}).sort()[0], g0 = (mem.gpus || {})[firstGpu] || {};
  const rows = [[W.layers, S.layers_on_card != null ? S.layers_on_card + ' of ' + S.layers_total : null],
    [W.ctx, S.ctx != null ? bmNum(S.ctx) + (S.slots ? ' / ' + bmNum(Math.floor(S.ctx / S.slots)) + ' (' + S.slots + ' slot' + (S.slots === 1 ? '' : 's') + ')' : '') : null],
    [W.kv, S.cache_type_k ? S.cache_type_k + (S.cache_type_v && S.cache_type_v !== S.cache_type_k ? ' / ' + S.cache_type_v : '') : null],
    [W.flash, S.flash_attn_active == null ? null : (S.flash_attn_active ? BM_WORDS.setup.on : BM_WORDS.setup.off)],
    [W.batch, S.n_batch ? S.n_batch + ' / ' + (S.n_ubatch || '') : null], [W.threads, S.threads || null],
    [W.ram, (mem.ram || {}).before_mb != null || (mem.ram || {}).highest_mb != null ? ((mem.ram.before_mb != null ? bmGb(mem.ram.before_mb) : BM_WORDS.res.notMeasured) + ' / ' + (mem.ram.highest_mb != null ? bmGb(mem.ram.highest_mb) : BM_WORDS.res.notMeasured)) : null],
    [W.loadTime, S.load_time_s != null ? bmNum(S.load_time_s, 1) + ' s' : null]];
  if (S.gpu_choice && S.gpu_choice.card) rows.push([W.gpuChoice, S.gpu_choice.card]);
  h += '<h4 class="bm-h2">' + esc(W.modelSettings) + '</h4><div class="bm-wrap">' + bmKV(rows) + '</div>';
  // memory: three separate things
  const hr = mem.headroom || {};
  const skyrim = mem.skyrim_state === 'closed' ? W.skyrimClosed : (mem.skyrim_state === 'typed' ? W.skyrimTyped : (mem.skyrim_peak_mb != null ? W.skyrimHistory(bmGb(mem.skyrim_peak_mb)) : W.skyrimNone));
  const cardTop = Math.max.apply(null, Object.keys(mem.gpus || {}).map(k => (mem.gpus[k] || {}).highest_mb).filter(v => v != null).concat([-1]));
  const cardTopMb = cardTop >= 0 ? cardTop : null;
  const modelMem = g0.highest_mb != null ? W.modelMem(g0.before_mb != null ? bmNum(g0.before_mb / 1024, 1) : BM_WORDS.res.notMeasured, g0.loaded_mb != null ? bmNum(g0.loaded_mb / 1024, 1) : BM_WORDS.res.notMeasured,
    bmGb(g0.at_top_level_mb != null ? g0.at_top_level_mb : g0.highest_mb), g0.at_top_level_mb != null ? d.level : null) : null;
  h += '<h4 class="bm-h2">' + esc(W.memory) + '</h4><div class="bm-wrap"><table class="ov-table bm-table"><colgroup><col style="width:16rem"><col></colgroup><tr><th>' + esc(W.what) + '</th><th>' + esc(W.value) + '</th></tr>' +
    '<tr><td>' + esc(mem.skyrim_state === 'closed' || mem.skyrim_state === 'typed' ? 'Skyrim' : W.skyrimRow) + '</td><td>' + esc(skyrim) + '</td></tr><tr><td>' + esc(W.modelRow) + '</td><td>' + (modelMem ? esc(modelMem) : bmNM('')) + '</td></tr>' +
    '<tr><td title="' + esc(BM_WORDS.res.headroomTip) + '">' + esc(W.headroomRow) + '</td><td>' + (hr.mb != null ? ovDot(bmLevel(hr.dot)) + esc(W.headroomAt(bmGb(hr.mb))) : bmHeadroomCell(hr)) + '</td></tr>' +
    ((d.others_at_start || []).length ? '<tr><td>' + esc(W.startedWith) + '</td><td>' + esc(d.others_at_start.map(p => p.name + ' ' + bmGb(p.mb)).join(', ')) + '</td></tr>' : '') + '</table></div>' +
    (cardTopMb != null ? '<div class="lm-muted" style="margin-top:6px">' + esc(W.cardHighest(bmGb(cardTopMb))) + '</div>' : '');
  // telemetry
  if ((d.telemetry || []).length) {
    h += '<h4 class="bm-h2">' + esc(W.telemetry) + '</h4><div class="bm-wrap"><table class="ov-table bm-table"><tr><th></th><th class="r">' + esc(W.lowest) + '</th><th class="r">' + esc(W.average) + '</th><th class="r">' + esc(W.highestWord) + '</th></tr>';
    const unit = { temp_c: v => bmNum(v) + ' °C', gpu_util_pct: v => bmNum(v) + '%', clock_mhz: v => bmNum(v) + ' MHz', power_w: v => bmNum(v) + ' W', fan_pct: v => bmNum(v) + '%', cpu_util_pct: v => bmNum(v) + '%' };
    const many = new Set(d.telemetry.filter(t => t.who !== 'machine').map(t => t.who)).size > 1;
    for (const t of d.telemetry) h += '<tr><td>' + esc((W.readings[t.reading] || t.reading) + (many && t.who !== 'machine' ? ' (' + W.gpu(t.who) + ')' : '')) + '</td><td class="r">' + esc(unit[t.reading](t.lowest)) + '</td><td class="r">' + esc(unit[t.reading](t.average)) + '</td><td class="r">' + esc(unit[t.reading](t.highest)) + '</td></tr>';
    h += '</table></div>';
  }
  // charts
  const C = d.charts || {}, firstOf = o => o ? o[Object.keys(o).sort()[0]] : null;
  const gen = C.generation_tok_s || [];
  const pts = gen.map((v, i) => [i, v]);
  const tSer = firstOf(C.temp_c), vSer = firstOf(C.vram_used_mb), pSer = firstOf(C.power_w);
  const avg = a => a.reduce((x, y) => x + y, 0) / a.length;
  h += '<div class="bm-charts">' + bmChart(W.genChart(gen.length), gen.length ? 'avg ' + bmNum(avg(gen)) + ', ' + bmNum(Math.min.apply(null, gen)) + ' to ' + bmNum(Math.max.apply(null, gen)) : '', W.request(gen.length), pts) +
    bmChart(BM_WORDS.det.readings.temp_c, tSer ? 'highest ' + bmNum(Math.max.apply(null, tSer.map(p => p[1]))) + ' °C' : '', W.wholeTest, tSer) +
    bmChart(W.vramChart, vSer ? 'highest ' + bmGb(Math.max.apply(null, vSer.map(p => p[1]))) : '', W.wholeTest, vSer) +
    bmChart(BM_WORDS.det.readings.power_w, pSer ? 'avg ' + bmNum(avg(pSer.map(p => p[1]))) + ' W, highest ' + bmNum(Math.max.apply(null, pSer.map(p => p[1]))) + ' W' : '', W.wholeTest, pSer) + '</div>';
  // checks (only when there are some)
  if ((d.checks || []).length) {
    h += '<h4 class="bm-h2">' + esc(W.checks) + '</h4><div class="bm-wrap"><table class="ov-table bm-table"><tr><th>' + esc(W.check) + '</th><th class="r">' + esc(W.passed) + '</th><th class="r">' + esc(W.of) + '</th></tr>';
    for (const c of d.checks) h += '<tr><td>' + esc(c.title) + '</td><td class="r">' + esc(c.passed) + '</td><td class="r">' + esc(c.total) + '</td></tr>';
    h += '</table></div>';
    const bad = d.checks.filter(c => (c.failures || []).length);
    if (bad.length) {
      h += '<details class="bm-fails"><summary>' + esc(W.viewFailed) + '</summary>';
      for (const c of bad) {
        h += '<div class="bm-q">' + esc(c.title) + '</div><ul class="bm-failist">' + c.failures.slice(0, 20).map(f => '<li><span class="lm-muted">' + esc(f.prompt_id || '') + (f.why ? ' \u2014 ' + esc(f.why) : '') + '</span><div class="bm-line" title="' + esc(f.line || '') + '">' + esc(f.line || '') + '</div></li>').join('') +
          (c.failures.length > 20 ? '<li class="lm-muted">' + esc(W.andMore(c.failures.length - 20)) + '</li>' : '') + '</ul>';
      }
      h += '</details>';
    }
  }
  h += '<div class="bm-msg" id="bmDetMsg">' + (bmMsg ? '<span class="lm-muted">' + esc(bmMsg) + '</span>' : '') + '</div><div class="bm-right">' + bmBtn(W.exportCsv, 'bmSave(\'' + id + '\', \'csv\')') + bmBtn(W.exportJson, 'bmSave(\'' + id + '\', \'json\')') + bmBtn(W.copy, 'bmCopySummary(this)') + '</div>';
  return h;
}
function bmSummaryText(d) {
  const row = d.row || {}, H = d.header || {}, lines = [];
  lines.push(String(H.model_file || row.model || '').replace(/\.gguf$/i, '') + (H.size_bytes ? ' (' + bmBytes(H.size_bytes) + ')' : ''));
  const top = (d.levels || []).slice(-1)[0];
  if (top) {
    if (top.generation_tok_s) lines.push(BM_WORDS.res.gen + ' at ' + top.level + ' concurrent: ' + bmNum(top.generation_tok_s.mean) + ' (' + bmRange(top.generation_tok_s, v => bmNum(v)) + ')');
    if (top.prompt_tok_s) lines.push(BM_WORDS.res.prompt + ' at ' + top.level + ' concurrent: ' + bmNum(top.prompt_tok_s.mean));
    if (top.ttft_ms) lines.push(BM_WORDS.res.ttft(top.level) + ': ' + bmSec(top.ttft_ms.mean) + ' (' + bmRange(top.ttft_ms, bmSec) + ')');
  }
  if (row.peak_vram_mb != null) lines.push(BM_WORDS.res.peakVram + ': ' + bmGb(row.peak_vram_mb));
  if (row.headroom && row.headroom.mb != null) lines.push(BM_WORDS.res.headroom + ': ' + bmGb(row.headroom.mb));
  lines.push(BM_WORDS.res.status + ': ' + bmRunStatus(row).word);
  return lines.join('\n');
}

// ---- the setup form (built once; polling never redraws it) ----
function bmIsCustom() {
  const a = bmSet.adv, d = BM_DEFAULT_SET;
  return String(bmSet.levels.slice().sort()) !== String(d.levels) || Number(bmSet.rpl) !== d.rpl || bmSet.prompts.length !== 3 || Number(a.temp || 0) !== 0 ||
    !!(a.ctx || a.layers || a.flash || a.batch || a.threads || a.cache || a.sustained || bmSet.gpu);
}
function bmOptSel(id, cur, opts) {
  return '<select id="' + id + '" onchange="bmAdv(\'' + id.slice(3).toLowerCase() + '\', this.value)">' + opts.map(o => '<option value="' + esc(o[0]) + '"' + (String(cur) === String(o[0]) ? ' selected' : '') + '>' + esc(o[1]) + '</option>').join('') + '</select>';
}
// The card the test will use is AMD (the one picked, or with Automatic the biggest real card): one short muted line, never for NVIDIA.
function bmAmdChosen(gp) {
  const cards = (gp && gp.cards) || [];
  if (!cards.length) return false;
  const picked = bmSet.gpu ? cards.find(c => c.id === bmSet.gpu) : null;
  const main = picked || cards.reduce((a, c) => ((c.memoryGb || 0) > (a.memoryGb || 0) ? c : a), cards[0]);
  return main.vendor === 'amd';
}
function bmAmdNoteHtml(gp) {
  const W = BM_WORDS.amd;
  return bmAmdChosen(gp) ? '<div class="bm-hint">' + esc(W.before) + '<a class="lm-link" href="' + esc(W.url) + '" target="_blank" rel="noopener noreferrer">' + esc(W.link) + '</a>' + esc(W.after) + '</div>' : '';
}
function bmSetupHtml(st) {
  const W = BM_WORDS.setup, a = bmSet.adv, sk = (st && st.skyrim) || {}, gp = (st && st.gpus) || {};
  const chk = (grp, v, label, checked, fn) => '<label class="bm-opt"><input type="checkbox" ' + (checked ? 'checked ' : '') + 'onchange="' + fn + '(\'' + v + '\', this.checked)"> <span>' + esc(label) + '</span></label>';
  let h = bmRemoteTestOnHtml() + (bmRemote.dev ? '' : bmAmdNoteHtml(gp) + '<div id="bmCard">' + bmCardHtml() + '</div>') + '<div class="bm-q">' + esc(W.concurrency) + '</div><div class="bm-row">' + [1, 4, 8].map(n => chk('lv', n, String(n), bmSet.levels.indexOf(n) >= 0, 'bmLevelTick')).join('') + '</div><div class="bm-hint">' + esc(W.concurrencyHint) + '</div>';
  h += '<div class="bm-q">' + esc(W.rpl) + '</div><div class="bm-row"><input class="bm-num" type="number" min="1" max="100" id="bmRpl" value="' + esc(bmSet.rpl) + '" oninput="bmSetRpl(this.value)"></div><div class="bm-hint" id="bmRplHint">' + esc(W.rplHint(bmSet.rpl)) + '</div>';
  h += '<div class="bm-q">' + esc(W.prompt) + '</div><div class="bm-row">' + ['short', 'medium', 'long'].map(c => chk('pr', c, W[c], bmSet.prompts.indexOf(c) >= 0, 'bmPromptTick')).join('') + '</div><div class="bm-hint">' + esc(W.promptHint) + '</div>';
  if (bmRemote.dev) h += bmRemoteWhileHtml(); else {
  h += '<div class="bm-q">' + esc(W.skyrim) + bmVram(true) + '</div>';
  const havePeak = sk.peakMb != null;
  if (sk.hasGame !== false) {
    if (havePeak) {
      h += '<label class="bm-opt"><input type="radio" name="bmSky" value="history" ' + (bmSet.skyrim === 'history' ? 'checked ' : '') + 'onchange="bmSky(\'history\')"> <span>' + esc(W.history) + '</span></label>' +
        '<div class="bm-hint" style="margin-left:1.6rem">' + esc(W.historyLine(bmNum(sk.peakMb / 1024, 1), sk.peakDay ? bmDay(sk.peakDay) : '')) + ' ' + bmBtn(W.reset, 'bmResetTracker()') + '</div>';
    } else {
      h += '<ol class="su-steps" style="margin:4px 0 4px 1.2rem">' + W.noHistory.map(t => '<li>' + esc(t) + '</li>').join('') + '</ol><div class="bm-hint">' + esc(W.noHistoryOr) + '</div>';
    }
  }
  h += '<label class="bm-opt"><input type="radio" name="bmSky" value="closed" ' + (bmSet.skyrim === 'closed' ? 'checked ' : '') + 'onchange="bmSky(\'closed\')"> <span>' + esc(W.closed) + '</span></label>' +
    '<label class="bm-opt"><input type="radio" name="bmSky" value="typed" ' + (bmSet.skyrim === 'typed' ? 'checked ' : '') + 'onchange="bmSky(\'typed\')"> <span>' + esc(W.typed) + '</span>' +
    '<input type="number" min="0" step="0.5" id="bmTyped" value="' + esc(bmSet.typed) + '" oninput="bmSet.typed=this.value;bmSaveSoon();bmRefit()" title="' + esc(W.typedLabel) + '" placeholder="GB"> <span class="lm-muted">' + esc(W.typedLabel) + '</span></label>';
  }
  h += '<div class="bm-q">' + esc(W.between) + '</div><label class="bm-opt"><input type="checkbox" ' + (bmSet.cool ? 'checked ' : '') + 'onchange="bmSet.cool=this.checked;bmSaveSoon()"> <span>' + esc(W.cool) + ': <span class="lm-muted">' +
    esc(W.coolPre) + ' <input class="bm-num" style="width:4.2rem" type="number" min="30" max="90" value="' + esc(bmSet.coolC) + '" oninput="bmSet.coolC=this.value;bmSaveSoon()"> ' + esc(W.coolPost) + '</span></span></label>';
  h += '<div class="bm-q">' + esc(W.safety) + '</div><label class="bm-opt"><input type="checkbox" ' + (bmSet.heat ? 'checked ' : '') + 'onchange="bmSet.heat=this.checked;bmSaveSoon()"> <span>' + esc(W.heat) + '</span></label>' +
    '<label class="bm-opt"><input type="checkbox" ' + (bmSet.mem !== false ? 'checked ' : '') + 'onchange="bmSet.mem=this.checked;bmSaveSoon()"> <span>' + esc(W.memGuard) + '</span></label><div class="bm-hint" style="margin:0 0 6px 1.6rem">' + esc(W.memGuardNote) + '</div>';
  if (gp.shown && !bmRemote.dev) {
    h += '<div class="bm-q">' + esc(W.gpus) + '</div><div class="bm-row"><select id="bmGpu" onchange="bmSet.gpu=this.value;bmChanged()"><option value="">' + esc(W.automatic) + '</option>' +
      (gp.cards || []).map(c => '<option value="' + esc(c.id) + '"' + (bmSet.gpu === c.id ? ' selected' : '') + '>' + esc(c.name + (c.memoryGb ? ' (' + bmNum(c.memoryGb, 0) + ' GB)' : '')) + '</option>').join('') + '</select></div><div class="bm-hint">' + esc(W.gpusHint) + '</div>';
  }
  const lay = [['', W.auto], ['all', W.all], ['none', W.none], ['number', W.number]], fl = [['', W.auto], ['on', W.on], ['off', W.off]], kv = [['', W.usual], ['f16', 'f16'], ['q8_0', 'q8_0'], ['q4_0', 'q4_0']];
  const inp = (key, ph) => '<input type="number" min="1" id="bmA' + key + '" value="' + esc(a[key]) + '" placeholder="' + esc(ph) + '" oninput="bmAdv(\'' + key + '\', this.value)">';
  h += '<details class="bm-adv"' + (bmIsCustom() && (a.ctx || a.layers || a.flash || a.batch || a.threads || a.cache || a.sustained || Number(a.temp || 0) !== 0) ? ' open' : '') + '><summary>' + esc(W.advanced) + '</summary><div class="bm-wrap"><table class="ov-table bm-table"><tr><th>' + esc(W.setting) + '</th><th>' + esc(W.value) + '</th><th>' + esc(W.why) + '</th></tr>' +
    '<tr><td>' + esc(W.ctx) + '</td><td>' + inp('ctx', W.usual) + '</td><td>' + esc(W.ctxWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.layers) + '</td><td>' + bmOptSel('bmLayers', a.layers, lay) + ' <input type="number" min="0" max="999" id="bmLayersN" style="width:5rem;' + (a.layers === 'number' ? '' : 'display:none') + '" value="' + esc(a.layersN) + '" oninput="bmAdv(\'layersn\', this.value)"></td><td>' + esc(W.layersWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.flash) + '</td><td>' + bmOptSel('bmFlash', a.flash, fl) + '</td><td>' + esc(W.flashWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.batch) + '</td><td>' + inp('batch', W.usual) + '</td><td>' + esc(W.batchWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.threads) + '</td><td>' + inp('threads', W.auto) + '</td><td>' + esc(W.threadsWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.temp) + '</td><td><input type="number" min="0" max="2" step="0.1" id="bmAtemp" value="' + esc(a.temp) + '" oninput="bmAdv(\'temp\', this.value)"></td><td>' + esc(W.tempWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.kv) + '</td><td>' + bmOptSel('bmCache', a.cache, kv) + '</td><td>' + esc(W.kvWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.sustained) + '</td><td><input type="number" min="1" max="30" id="bmAsustained" value="' + esc(a.sustained) + '" placeholder="5" oninput="bmAdv(\'sustained\', this.value)"></td><td>' + esc(W.sustainedWhy) + '</td></tr>' +
    '<tr><td>' + esc(W.slots) + '</td><td colspan="2">' + esc(W.slotsValue) + '</td></tr></table></div>' +
    '<div class="bm-row" style="margin-top:8px"><span class="lm-muted">' + esc(W.saved) + '</span> ' + bmBtn(W.resetDefaults, 'bmResetDefaults()') + '</div></details>';
  h += '<div class="bm-note" id="bmCustomLine"></div>' + bmStartRowHtml(W);
  return h;
}
function bmDay(iso) { try { return new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }); } catch (e) { return iso; } }

// ---- the plan the page sends ----
function bmPlan() {
  const a = bmSet.adv, over = {};
  if (a.ctx) over.ctx = parseInt(a.ctx, 10);
  if (a.layers === 'number') { if (a.layersN !== '') over.layers = parseInt(a.layersN, 10); } else if (a.layers) over.layers = a.layers;
  if (a.flash) over.flashAttn = a.flash;
  if (a.batch) over.batch = parseInt(a.batch, 10);
  if (a.threads) over.threads = parseInt(a.threads, 10);
  if (a.cache) over.cacheType = a.cache;
  if (bmSet.gpu) over.gpu = bmSet.gpu;
  const skyrim = bmSet.skyrim === 'closed' ? 'closed' : (bmSet.skyrim === 'typed' ? 'available:' + (parseFloat(bmSet.typed) || 0) : 'history');
  const mins = parseFloat(a.sustained);
  const sus = mins > 0 ? { sustained_s: Math.max(60, Math.min(1800, Math.round(mins * 60))) } : {};
  return Object.assign(sus, { levels: bmSet.levels.slice().sort((x, y) => x - y), requests_per_level: Math.max(1, Math.min(100, parseInt(bmSet.rpl, 10) || 25)), prompts: bmSet.prompts.slice(), temperature: parseFloat(a.temp) || 0,
    settings: over, skyrim, cooldown: !!bmSet.cool, cooldown_c: parseFloat(bmSet.coolC) || 55, heat_stop: !!bmSet.heat, memory_guard: bmSet.mem !== false });
}
function bmTickedKeys() { return ((bmTab && bmTab.models) || []).filter(r => r.ticked).map(r => r.key); }

// ---- loading, polling, actions ----
async function bmGet(url) { const r = await fetch(url); if (!r.ok) { let d = {}; try { d = await r.json(); } catch (e) {} throw new Error(d.detail || 'Something went wrong (' + r.status + ')'); } return r.json(); }
let bmListMsg = '', bmQueueSeen = null;
function bmOpened() { bmLoadAll(); bmSchedule(); }
function bmClosed() { if (bmTimer) { clearTimeout(bmTimer); bmTimer = null; } }
function bmSchedule() {
  if (bmTimer) clearTimeout(bmTimer);
  const busy = (bmStatus && bmStatus.running) || bmRemoteOn() || bmQueueOn() || ((bmTab && bmTab.models) || []).some(r => r.download && r.download.state === 'running');
  bmTimer = setTimeout(bmPollTick, busy ? 1500 : 5000);
}
async function bmPollTick() {
  bmTimer = null;
  const el = document.getElementById('paneBenchmarks');
  if (!el || el.style.display === 'none') return;
  await bmLoadStatus();
  await bmRemoteLoadState();
  const downloading = bmQueueOn() || ((bmTab && bmTab.models) || []).some(r => r.download && (r.download.state === 'running'));
  if (downloading) await bmLoadTab(false);
  bmSchedule();
}
function bmQueueOn() { return !!(bmTab && bmTab.queue && bmTab.queue.active); }
async function bmLoadAll() { await bmLoadCard(true); await bmLoadTab(true); await bmRemoteLoadDevices(); await bmLoadStatus(); await bmRemoteLoadState(); await bmLoadResults(); }
async function bmLoadTab(rebuild) {
  try { bmTab = await bmGet('/config/benchmarks/tab'); } catch (e) { return; }
  bmQueueDone();
  const first = !bmSetupBuilt;
  if (!bmSetupBuilt) { Object.assign(bmSet, JSON.parse(JSON.stringify(BM_DEFAULT_SET)), bmTab.setup || {}); bmSet.adv = Object.assign({}, BM_DEFAULT_SET.adv, (bmTab.setup || {}).adv || {}); }
  if (bmSet.skyrim === 'history' && bmTab.skyrim && bmTab.skyrim.peakMb == null) bmSet.skyrim = 'closed';             // no history yet: the first choice is not on offer
  const key = JSON.stringify([bmTab.skyrim, bmTab.gpus && bmTab.gpus.shown, (bmTab.gpus && bmTab.gpus.cards || []).map(c => c.id)]);
  const box = document.getElementById('bmModels');
  if (box) box.innerHTML = bmModelsHtml(bmTab);
  if (key !== bmSetupBuilt) { bmSetupBuilt = key; const s = document.getElementById('bmSetup'); if (s) s.innerHTML = bmSetupHtml(bmTab); }
  bmUpdateBits();
  if (first && bmTab.models && bmTab.models.length) bmRefit();                       // the dots follow the levels and the settings the page shows, from the first look
}
// While a test runs (here or on another PC) the two setup panels fold to their heading, like the folded cards elsewhere on the dashboard. The person can open either by clicking its heading;
// that choice is kept until the test starts or ends, and then both follow the run again (folded while it runs, open when it is over).
let bmFoldUser = { pick: null, setup: null }, bmFoldPrev = false;
function bmIsRunning() { return !!(bmStatus && bmStatus.running) || bmRemoteOn(); }
function bmFoldApply() {
  const run = bmIsRunning();
  if (run !== bmFoldPrev) { bmFoldPrev = run; bmFoldUser = { pick: null, setup: null }; }
  [['pick', 'bmPickHead', ['bmModels']], ['setup', 'bmSetupHead', ['bmEngine', 'bmSetup']]].forEach(([k, head, bodies]) => {
    const folded = bmFoldUser[k] != null ? bmFoldUser[k] : run;
    const h = document.getElementById(head), a = h && h.querySelector ? h.querySelector('.bm-arrow') : null;
    if (a) a.textContent = folded ? '▶' : '▼';
    bodies.forEach(id => { const el = document.getElementById(id); if (el && el.style) el.style.display = folded ? 'none' : ''; });
  });
}
function bmFoldToggle(k) { const cur = bmFoldUser[k] != null ? bmFoldUser[k] : bmIsRunning(); bmFoldUser[k] = !cur; bmFoldApply(); }
async function bmLoadStatus() {
  let st = null;
  try { st = await bmGet('/config/benchmarks/status'); } catch (e) { return; }
  const was = bmWasRunning;
  bmStatus = st; bmWasRunning = !!st.running;
  if (st.server_now_epoch_s) bmSkew = st.server_now_epoch_s - Date.now() / 1000;
  if (!bmClockId) bmClockId = setTimeout(bmClockLoop, 1000);
  const box = document.getElementById('bmRun');
  if (box) box.innerHTML = bmRunHtml(st);
  bmDrawCard();
  if (st.running) { if (bmLastStep !== st.step) { bmLastStep = st.step; bmLoadResults(); } }
  else if (was) { bmLoadResults(); bmLoadTab(false); }
  bmUpdateBits();
}
async function bmLoadResults() {
  try { bmResults = (await bmGet('/config/benchmarks/results')).runs || []; } catch (e) { return; }
  bmRenderResults();
}
// a run that measured nothing (it failed at the start) is a past run, never the current Results
function bmMeasured(r) { return r.status !== 'newer_version' && (r.requests > 0 || !!r.generation_tok_s || !!r.prompt_tok_s || !!r.ttft_ms); }
function bmLatestBatch() { const r = (bmResults || []).find(x => x.batch_id && bmMeasured(x)); return r ? r.batch_id : null; }
function bmRenderResults() {
  const res = document.getElementById('bmResults'), past = document.getElementById('bmPast'), pc = document.getElementById('bmPastCard');
  if (!res) return;
  if (bmView === 'detail' && bmDetail) { res.innerHTML = bmDetailHtml(bmDetail); if (pc) pc.style.display = 'none'; return; }
  const latest = bmLatestBatch();
  const shown = r => r.status === 'newer_version' || (bmMeasured(r) && (!latest || r.batch_id === latest));
  const rows = (bmResults || []).filter(shown);
  const old = (bmResults || []).filter(r => !shown(r));
  res.innerHTML = bmResultsHtml(rows.slice().sort((a, b) => (a.started_epoch_s || 0) - (b.started_epoch_s || 0)), bmTicks);
  if (pc) { pc.style.display = old.length ? '' : 'none'; bmPastIds = old.filter(r => r.status !== 'newer_version').map(r => r.run_id); if (past) past.innerHTML = bmPastHtml(old); }
}
function bmEngineWhy() {
  const e = bmTab && bmTab.engine, W = BM_WORDS.setup;
  return e === 'local_models_off' ? W.modelsOff : (e === 'not_installed' ? W.noEngine : '');
}
function bmEngineHtml() {
  const why = bmEngineWhy(), W = BM_WORDS.setup;
  if (!why) return '';
  const off = bmTab.engine === 'local_models_off';
  return '<div class="lm-statusline" style="margin:8px 0"><span class="lm-warn">⚠️ ' + esc(why) + '</span><span style="margin-left:auto">' + bmBtn(off ? W.setUpLocal : W.openLocal, off ? 'bmSetUpLocal()' : "showMainTab('local')") + '</span></div>';
}
async function bmSetUpLocal() {
  try { await lmPost('/config/local-models/skip', { skip: false }); } catch (e) { bmShowErr(e.message); return; }
  showMainTab('local'); if (typeof lmRefresh === 'function') lmRefresh();
}
function bmUpdateBits() {
  bmFoldApply();
  const eng = document.getElementById('bmEngine');
  if (eng) { const h = bmEngineHtml(); if (eng.innerHTML !== h) eng.innerHTML = h; }
  const b = document.getElementById('bmStartBtn');
  if (b) { const why = bmEngineWhy(); b.disabled = !!(bmStatus && bmStatus.running) || bmRemoteOn() || bmQueueOn() || (bmRemote.dev ? false : !!why); b.title = bmRemote.dev ? '' : why; }
  const est = document.getElementById('bmEstimate');
  if (est && bmRemote.dev) est.textContent = bmRemoteEstimate();
  const c = document.getElementById('bmCustomLine');
  if (c && bmRemote.dev) { c.innerHTML = bmIsCustom() ? '<span class="lm-muted">' + esc(BM_WORDS.setup.customLine) + '</span>' : ''; return; }
  if (c) c.innerHTML = bmIsCustom() ? '<span class="lm-muted">' + esc(BM_WORDS.setup.customLine) + '</span>' : '';
  const h = document.getElementById('bmRplHint');
  if (h) h.textContent = BM_WORDS.setup.rplHint(Math.max(1, Math.min(100, parseInt(bmSet.rpl, 10) || 25)));
}
function bmSoon() { setTimeout(() => { bmLoadTab(false); bmSchedule(); }, 400); }
function bmSaveSoon() { bmUpdateBits(); clearTimeout(bmSaveTimer); bmSaveTimer = setTimeout(bmSaveNow, 600); }
function bmChanged() { bmSaveSoon(); bmRefit(); }
function bmSaveNow() { lmPost('/config/benchmarks/setup', { ticked: bmTickedKeys(), setup: bmSet }).catch(() => {}); }
function bmLevelTick(n, on) { n = parseInt(n, 10); const i = bmSet.levels.indexOf(n); if (on && i < 0) bmSet.levels.push(n); if (!on && i >= 0) bmSet.levels.splice(i, 1); bmChanged(); }
function bmPromptTick(c, on) { const i = bmSet.prompts.indexOf(c); if (on && i < 0) bmSet.prompts.push(c); if (!on && i >= 0) bmSet.prompts.splice(i, 1); bmChanged(); }
function bmSetRpl(v) { bmSet.rpl = v; bmChanged(); }
function bmAdv(k, v) {
  if (k === 'layers') { bmSet.adv.layers = v; const n = document.getElementById('bmLayersN'); if (n) n.style.display = v === 'number' ? '' : 'none'; }
  else if (k === 'layersn') bmSet.adv.layersN = v;
  else if (k === 'flash') bmSet.adv.flash = v;
  else if (k === 'cache') bmSet.adv.cache = v;
  else bmSet.adv[k] = v;
  bmChanged();
}
function bmSky(v) { bmSet.skyrim = v; bmChanged(); bmRefit(); }
// the dots follow the Skyrim choice at once: the server judges the models again for it (the same verdict as the Local device tab)
let bmFitTimer = null;
// the dots and the question at Start use the same inputs: the Skyrim choice, the levels, the prompt lengths and the changed engine settings
function bmFitUrl(mode) {
  const settings = bmPlan().settings || {};
  return '/config/benchmarks/fit?mode=' + mode + '&gb=' + encodeURIComponent(bmSet.typed || '') + '&levels=' + bmSet.levels.join(',') + '&prompts=' + bmSet.prompts.join(',') + '&ov=' + encodeURIComponent(JSON.stringify(settings));
}
function bmRefit() {
  clearTimeout(bmFitTimer);
  bmFitTimer = setTimeout(async () => {
    if (!bmTab) return;
    try {
      const mode = bmSet.skyrim === 'typed' ? 'typed' : (bmSet.skyrim === 'closed' ? 'closed' : 'history');
      const r = await bmGet(bmFitUrl(mode));
      for (const m of bmTab.models || []) { const f = r.fit[m.key]; if (f) Object.assign(m, f); }
      bmRedrawList();
    } catch (e) {}
  }, 250);
}
async function bmResetTracker() { try { await lmPost('/config/local-models/memory/reset', {}); } catch (e) {} bmSetupBuilt = ''; bmLoadTab(true); }
function bmResetDefaults() {
  const keep = { skyrim: bmSet.skyrim, typed: bmSet.typed };
  bmSet = JSON.parse(JSON.stringify(BM_DEFAULT_SET)); Object.assign(bmSet, keep);
  bmSetupBuilt = ''; bmSaveNow(); bmLoadTab(true);
}
function bmRedrawList() { const box = document.getElementById('bmModels'); if (box && bmTab) box.innerHTML = bmModelsHtml(bmTab); bmUpdateBits(); }
function bmTick(key, on) { const m = ((bmTab && bmTab.models) || []).find(r => r.key === key); if (m) m.ticked = on; bmSaveNow(); bmRedrawList(); }
function bmTickAll(on) { ((bmTab && bmTab.models) || []).forEach(r => { if (r.state !== 'missing') r.ticked = on; }); bmSaveNow(); bmRedrawList(); }
function bmResTick(id, on) { bmTicks[id] = on; bmRenderResults(); }
function bmPastTick(id, on) { bmPastTicks[id] = on; bmRenderResults(); }
function bmPastSelect(how) { for (const id of bmPastIds) bmPastTicks[id] = how === 'all' ? true : how === 'clear' ? false : !bmPastTicks[id]; bmRenderResults(); }
function bmPastDeleteTicked() { if (bmStatus && bmStatus.running) return; const ids = bmPastIds.filter(id => bmPastTicks[id]); if (ids.length) return bmDeleteRuns(ids, ''); }
async function bmAddFile() {
  try { const r = await lmPost('/config/benchmarks/add-file', {}); if (r.key) { await bmLoadTab(false); bmTick(r.key, true); await bmLoadTab(false); } } catch (e) { bmShowErr(e.message); }
}
async function bmDownloadIds(ids, then) {
  bmListMsg = '';
  try {
    if (!(await lmEnsureFolder())) return false;
    await lmPost('/config/benchmarks/download', then ? { ids: ids, then: then } : { ids: ids });
  } catch (e) { bmListMsg = e.message; bmRedrawList(); return false; }
  await bmLoadTab(false); bmSchedule();
  return true;
}
function bmDownload(id) { bmDownloadIds([id]); }
function bmResume(id) { bmDownloadIds([id]); }
function bmDownloadTicked() { bmDownloadIds(bmPendingOf((bmTab && bmTab.models) || []).ids); }
async function bmCancelDl(id) { try { await lmPost('/config/benchmarks/download/cancel', { id: id }); } catch (e) {} await bmLoadTab(false); bmSchedule(); }
async function bmQueueDone() {
  const q = bmTab && bmTab.queue;
  if (!q) return;
  if (bmQueueSeen === null && !q.active) { bmQueueSeen = q.id; return; }
  if (q.active || q.id === bmQueueSeen) return;
  bmQueueSeen = q.id;
  const r = q.start_result;
  if (!q.will_start || !r) return;
  if (r.ok) { await bmLoadStatus(); bmShowRun(); return; }
  if (r.needs_takeover) {
    const names = r.needs_takeover.map(m => m.name).join(', ');
    if (await ownAsk((r.message || '') + (names ? ' (' + names + ')' : ''), BM_WORDS.setup.stopIt)) await bmSend(q.body, true);
    return;
  }
  bmShowErr(r.message || '');
}
async function bmRemove(key) {
  const m = ((bmTab && bmTab.models) || []).find(r => r.key === key);
  if (!m) return;
  try {
    if (m.source === 'local') {
      if (!(await ownAsk(BM_WORDS.models.removeAsk(m.title), BM_WORDS.models.remove))) return;
      await lmPost('/config/benchmarks/remove-file', { path: m.path });
    } else if (m.state === 'downloaded') {
      const r = await lmRemoveFlow(m.id, m.title, false, bmBytes(m.sizeBytes) || '', []);
      if (!r) return;
      await lmPost('/config/local-models/remove', { id: m.id, deleteFiles: r.deleteFiles });
      if (typeof lmRefresh === 'function') lmRefresh();
    } else { await lmPost('/config/benchmarks/hide', { id: m.id }); }
  } catch (e) { bmShowErr(e.message); }
  await bmLoadTab(false);
}
function bmShowErr(t) { const e = document.getElementById('bmStartMsg'); if (e) e.innerHTML = bmWarn(t); }
// ---- the question at Start: a model that needs more graphics memory than is free at this many requests at once ----
// The same numbers as the dots (the server's `benchmarks_estimate`). One pop-up per Start; what the person answered is remembered only for this same plan on this same device, and
// any change of models, levels, prompts, settings, Skyrim choice or device asks again. Turning the memory guard off does not stop the question.
let bmAck = null;
function bmAckSig(body, device) {
  return JSON.stringify({ d: device || 'this', m: (body.models || body.keys || []).slice().sort(), l: body.levels, p: body.prompts, s: body.settings, k: body.skyrim });
}
function bmAskSentence(W, o, where, usage) {
  const gb = mb => bmNum(mb / 1024, 1);
  if (!usage || !usage.available) return W.fitAskSentence(o.name, gb(o.needMb), o.slots, gb(o.freeMb), where);                  // another PC, or the card could not be read: the usual sentence
  const names = (usage.programs || []).slice(0, 3).map(p => p.name);
  return W.fitAskNow(o.name, gb(o.needMb), o.slots, gb(o.freeMb), where, o.parts ? gb(o.parts.modelMb) : null, o.parts ? gb(o.parts.cacheMb) : null, names, usage.usedMb != null ? gb(usage.usedMb) : '');
}
function bmAskFit(over, where, usage) {
  const W = BM_WORDS.models;
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'op-modal';
    wrap.innerHTML = '<div class="op-modal-box" role="alertdialog" aria-modal="true" style="width:min(560px,100%)"><div class="op-confirm" id="bmFitText"></div>' +
      '<div class="op-btns" style="justify-content:flex-end"><button class="ghost" id="bmFitCancel"></button><button id="bmFitLeave"></button><button class="ghost" id="bmFitRun"></button></div></div>';
    const box = wrap.querySelector('#bmFitText');
    for (const o of over) { const line = document.createElement('div'); line.style.margin = '0 0 8px'; line.textContent = bmAskSentence(W, o, where, usage); box.appendChild(line); }
    wrap.querySelector('#bmFitCancel').textContent = W.fitAskCancel;
    wrap.querySelector('#bmFitLeave').textContent = W.fitAskLeave;
    wrap.querySelector('#bmFitRun').textContent = W.fitAskRun;
    const prevFocus = document.activeElement;
    const done = ans => { document.removeEventListener('keydown', onKey, true); wrap.remove(); if (prevFocus && prevFocus.focus) try { prevFocus.focus(); } catch (e) {} resolve(ans); };
    const onKey = ev => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); done('cancel'); } };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('click', ev => { if (ev.target === wrap) done('cancel'); });
    wrap.querySelector('#bmFitCancel').onclick = () => done('cancel');
    wrap.querySelector('#bmFitLeave').onclick = () => done('leave');
    wrap.querySelector('#bmFitRun').onclick = () => done('run');
    document.body.appendChild(wrap);
    wrap.querySelector('#bmFitCancel').focus();
  });
}
// -> { body, models } to go on (maybe with models left out), or null when the person cancelled. `models`: the ticked list rows (so a model left out is unticked).
async function bmGuardStart(body, models) {
  let over = [], usage = null;
  try {
    const r = await lmPost('/config/benchmarks/precheck', body);
    usage = r.usage || null;
    if (usage) { bmCard = usage; bmDrawCard(); }                                                    // the card was read again at Start
    over = (r.rows || []).filter(x => x.fit === 'too_big').map(x => ({ path: x.path, name: x.name, needMb: x.fitNeedMb, freeMb: x.fitFreeMb, slots: x.fitSlots, parts: x.fitParts }));
    const listed = (models || []).filter(m => !m.path && m.fit === 'too_big' && m.fitNeedMb != null);           // ticked but not downloaded yet: judged from the list's own numbers
    over = over.concat(listed.map(m => ({ key: m.key, name: String(m.title).replace(/\.gguf$/i, ''), needMb: m.fitNeedMb, freeMb: m.fitFreeMb, slots: m.fitSlots, parts: m.fitParts })));
  } catch (e) { return { body: body, models: models }; }               // it cannot be checked: the test is not held back for that
  if (!over.length) return { body: body, models: models };
  const sig = bmAckSig(body);
  if (bmAck === sig) return { body: body, models: models };
  const ans = await bmAskFit(over, BM_WORDS.models.fitAskHere, usage);
  if (ans === 'run') { bmAck = sig; return { body: body, models: models }; }
  if (ans !== 'leave') return null;
  const out = (models || []).filter(m => over.some(o => (o.key && o.key === m.key) || (o.path && o.path === m.path)));
  for (const m of out) m.ticked = false;
  const keep = (body.models || []).filter(p => !over.some(o => o.path === p));
  const left = (models || []).filter(m => m.ticked);
  if (!keep.length && !left.length) { bmSaveNow(); bmRedrawList(); return null; }
  bmSaveNow(); bmRedrawList();
  return { body: Object.assign({}, body, { models: keep }), models: left };
}

async function bmStart() {
  if (bmRemote.dev) return bmRemoteStart(false);
  const W = BM_WORDS.setup, msg = document.getElementById('bmStartMsg');
  if (msg) msg.innerHTML = '';
  let models = ((bmTab && bmTab.models) || []).filter(r => r.ticked);
  if (!models.length) { bmShowErr(W.pickModel); return; }
  if (!bmSet.levels.length) { bmShowErr(W.pickLevel); return; }
  if (!bmSet.prompts.length) { bmShowErr(W.pickPrompt); return; }
  await bmSaveNow();
  let body = bmPlan();
  if (bmTab && bmTab.skyrim && bmTab.skyrim.running && bmTab.skyrim.hasGame !== false) {        // Skyrim is running on THIS PC and the test runs here: ask once
    if (!(await ownAsk(BM_WORDS.remote.skyrimQ, BM_WORDS.remote.runAnyway))) return;
    if (body.skyrim === 'closed') body.skyrim = 'history';                                       // ("Game closed" would refuse; the result records that Skyrim was running)
  }
  body.models = models.map(m => m.path).filter(Boolean);
  try {
    const g = await bmGuardStart(body, models);
    if (!g) return;
    body = g.body; models = g.models;
    const pend = bmPendingOf(models);
    if (pend.count) { await bmDownloadIds(pend.ids, body); return; }
    await bmSend(body, false);
  } finally { bmAck = null; }
}
// Once, when a test has just started: bring the live panel (right under Set up the test) into view. The poll never calls this, so a person who scrolls away is left alone.
function bmShowRun() { const el = document.getElementById('bmRun'); if (el && el.scrollIntoView && el.innerHTML) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
async function bmSend(body, takeover) {
  const r = await fetch('/config/benchmarks/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({}, body, takeover ? { takeover: true } : {})) });
  let d = {};
  try { d = await r.json(); } catch (e) {}
  if (r.ok && d.ok) { bmMsg = ''; await bmLoadStatus(); bmShowRun(); return; }
  if (d.needs_takeover && !takeover) {
    const names = d.needs_takeover.map(m => m.name).join(', ');
    if (await ownAsk((d.detail || '') + (names ? ' (' + names + ')' : ''), BM_WORDS.setup.stopIt)) return bmSend(body, true);
    return;
  }
  bmShowErr(d.detail || 'Something went wrong (' + r.status + ')');
}
async function bmStop() { if (bmRemoteOn()) return bmRemoteStop(); try { await lmPost('/config/benchmarks/cancel', {}); } catch (e) {} await bmLoadStatus(); await bmLoadResults(); }

// results actions
function bmTickedRuns() { return Object.keys(bmTicks).filter(k => bmTicks[k]); }
async function bmPlanOf(id) { return bmGet('/config/benchmarks/runs/' + encodeURIComponent(id) + '/plan'); }
async function bmRepeat(id) {
  try { const p = await bmPlanOf(id); const g = await bmGuardStart(p, []); if (!g) return; bmBack(); await bmSend(g.body, false); } catch (e) { bmMsg = e.message; bmRenderResults(); } finally { bmAck = null; }
}
async function bmTestAgain() {
  const ids = bmTickedRuns();
  if (!ids.length) return;
  const rem = (bmResults || []).filter(x => ids.indexOf(x.run_id) >= 0 && x.device && x.device.kind === 'courier');
  if (rem.length) return bmRemoteAgain(rem);
  try {
    const plans = await Promise.all(ids.map(bmPlanOf));
    const body = Object.assign({}, plans[0], { models: plans.map(p => p.models[0]) });
    const g = await bmGuardStart(body, []);
    if (!g) return;
    await bmSend(g.body, false);
  } catch (e) { bmMsg = e.message; bmRenderResults(); }
}
async function bmAddToMp() {
  let said = '';
  for (const id of bmTickedRuns()) {
    const rr = (bmResults || []).find(x => x.run_id === id);
    if (rr && rr.device && rr.device.kind === 'courier') { try { const a = await lmPost('/config/benchmarks/remote/add', { run_id: id }); said = a.onDevice ? BM_WORDS.remote.addOnDevice(String(rr.model).replace(/\.gguf$/i, ''), a.device || rr.device.name) : BM_WORDS.remote.addNotThere(a.device || rr.device.name); } catch (e) { said = e.message; } continue; }
    try { const r = await lmPost('/config/benchmarks/add-to-multiproxy', { run_id: id }); said = r.already ? BM_WORDS.res.addedAlready : BM_WORDS.res.added; } catch (e) { said = e.message; }
  }
  bmMsg = said; bmRenderResults();
  if (typeof mdLoad === 'function') mdLoad();
}
function bmViewFirst() { const ids = bmTickedRuns(); if (ids.length) bmOpen(ids[0]); }
async function bmExportAll() { try { await lmPost('/config/benchmarks/save-results', {}); } catch (e) { bmMsg = e.message; bmRenderResults(); } }
async function bmOpen(id) {
  try { bmDetail = await bmGet('/config/benchmarks/runs/' + encodeURIComponent(id) + '/detail'); } catch (e) { bmMsg = e.message; bmRenderResults(); return; }
  bmView = 'detail'; bmMsg = ''; bmRenderResults();
  const el = document.getElementById('bmResults'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
}
function bmBack() { bmView = 'list'; bmDetail = null; bmMsg = ''; bmRenderResults(); }
async function bmSave(id, fmt) {
  try { await lmPost('/config/benchmarks/runs/' + encodeURIComponent(id) + '/save', { format: fmt }); } catch (e) { bmMsg = e.message; bmRenderResults(); }
}
function bmCopySummary(btn) { if (bmDetail && typeof copyModel === 'function') copyModel(btn, bmSummaryText(bmDetail)); }
async function bmDeleteRuns(ids, name) {
  if (!(await ownAsk(ids.length > 1 ? BM_WORDS.past.askMany : BM_WORDS.past.ask(name), BM_WORDS.past.go))) return;
  for (const id of ids) {
    try { await fetch('/config/benchmarks/runs/' + encodeURIComponent(id), { method: 'DELETE', headers: { 'X-MultiProxy': '1' } }); } catch (e) {}
    delete bmTicks[id]; delete bmPastTicks[id];
  }
  await bmLoadResults();
}
function bmDelete(id, name) { return bmDeleteRuns([id], name); }
function bmDeleteTicked() {
  if (bmStatus && bmStatus.running) return;
  const ids = bmTickedRuns(), names = ids.map(id => { const r = (bmResults || []).find(x => x.run_id === id); return r ? String(r.model).replace(/\.gguf$/i, '') : ''; }).filter(Boolean);
  if (ids.length) return bmDeleteRuns(ids, names.join(', '));
}

// ---------------- a test on another PC (design/mockup-benchmarks-remote.html, revision 5: the words below are the mockup's) ----------------
BM_WORDS.remote = {
  testOn: 'Test on', thisPc: 'This PC', skyrimHere: 'Skyrim runs here', vramWord: 'VRAM', runs: n => 'runs ' + n, voice: 'Voice', decisions: 'Decisions', courier: v => 'Courier ' + v,
  ready: 'Online, idle', thisReady: 'Ready', offline: 'Offline', needsUpdate: 'Needs an update', lastSeen: ago => 'Last seen ' + ago + ' ago', outOfDate: 'Its Courier is out of date. Update it first from Other devices.',
  checking: 'Checking', checkFailed: 'Couldn\'t check the models on this device.',
  onDevice: d => 'On ' + d, alreadyThere: 'Already there', notThere: 'Not there', what: 'What happens', asIs: 'Used as it is', sent: 'Sent from this PC', sentTight: 'Sent from this PC. Tight fit on this card.', tooBig: (d, gb) => 'Too big for ' + d + ' (' + gb + ')',
  sending: (size, d, t, speed) => 'Sending ' + size + ' to ' + d + (t ? ' (about ' + t + ' at the last speed, ' + speed + ')' : '') + '.', free: (d, free) => d + ' has ' + free + ' free.', deleted: d => 'Each model is deleted from ' + d + ' after its test unless you tick Keep.',
  nothingToSend: d => 'Nothing to send: the ticked models are already on ' + d + '.',
  keep: d => 'Keep them on ' + d, keepNote: d => 'A model that isn\'t on this PC yet is downloaded here first, then sent. Keep them on ' + d + ' leaves the tested models there as normal models (they show up in its list on Other devices).',
  whileTesting: 'While testing', loadUsual: d => 'Load ' + d + '\'s usual services, then test', loadNote: names => 'Loads ' + names + ', so you see the memory left next to them. They\'re loaded, not busy.', loadNone: d => 'Nothing is set up to load on ' + d + '.',
  leave: 'Leave things as they are', leaveNote: (names, gb) => names + (names.indexOf(' and ') >= 0 ? ' are' : ' is') + ' running now' + (gb ? ' (' + gb + ')' : '') + '.', leaveNone: 'Nothing is running now.',
  stop: d => 'Stop what MultiProxy started on ' + d + ', then test', stopNote: 'Gives the test the whole card.', quickDecision: 'the quick-decision model',
  battery: d => 'Keep ' + d + ' plugged in, lid open and awake. The test stops if it sleeps, the lid closes or the battery gets low.',
  estimate: (n, min, send) => 'About ' + min + ' min for ' + n + ' model' + (n === 1 ? '' : 's') + (send ? ', plus ' + send + ' to send the files' : '') + '.', start: d => 'Start benchmark on ' + d,
  skyrimQ: 'Skyrim is running. Run the test anyway? The game and test will share the graphics card, so speeds will reflect that. The results will note Skyrim was running.', runAnyway: 'Run anyway',
  takeOverAsk: d => d + ' is answering game chat. Taking over pauses its NPC voices and decisions until the test ends, then starts them again.', takeOver: d => 'Take over ' + d,
  noSpace: (d, free, need) => d + ' has ' + free + ' free and this test needs about ' + need + '. Untick a model or free up space on ' + d + '.', busyTest: d => 'Another test is already running on ' + d + '. Wait for it to finish.',
  isOffline: d => d + ' is offline. Turn it on or check its network connection.', oldCourier: d => d + '\'s Courier is out of date. Update it first from Other devices.', downloadFirst: 'Download the ticked models first.',
  phases: d => ['Preparing', 'Sending the model to ' + d, 'Testing', 'Bringing the results back'], model: (k, n, name, d) => 'Model ' + k + ' of ' + n + ': ' + name + ' on ' + d,
  sendLine: (sizes, speed, left) => sizes + (speed ? ' · ' + speed : '') + (left ? ' · about ' + left + ' left' : ''), stopHint: d => 'Stopping also unloads the engine and deletes the files sent to ' + d + '.',
  usingVram: (name, gb) => name + ' is using ' + gb,
  lost: (d, k, n, saved) => 'Lost contact with ' + d + (n ? ' at step ' + k + ' of ' + n : '') + '. It stops the test after 60 seconds without contact, and this PC will confirm when it\'s back.' + (saved ? ' ' + (saved === 1 ? '1 finished result is saved here.' : saved + ' finished results are saved here.') : ''),
  slept: (d, model) => d + ' went to sleep during ' + model + '. That run was interrupted and the rest were not started.', lostEnded: d => d + ' stopped the test because it lost contact with this PC.',
  coming: 'Results coming back', saved: 'Saved on this PC', waiting: d => 'Finished on ' + d + ', waiting to collect', waitingWhy: d => '(this PC was offline; it collects them when ' + d + ' is back)', collecting: d => 'Collecting from ' + d,
  failedCollect: 'Failed to collect', notRun: 'Not run',
  resultsFrom: d => 'from ' + d, services: (names, d) => 'With ' + names + ' loaded on ' + d + ' for every model.', hot: d => d + ' was running hot', onBattery: d => d + ' was on battery.',
  device: 'Device', devFact: (name, card, driver) => 'Device: ' + name + (card ? ' (' + card + (driver ? ', driver ' + driver : '') + ')' : ''), skyrimRunning: 'Skyrim was running',
  addOnDevice: (m, d) => m + ' is already set up on ' + d + '.', addNotThere: d => 'It isn\'t on ' + d + ' any more. Run it again with Keep them on ' + d + ' ticked to put it there.' };
let bmRemote = { devices: [], dev: '', plan: null, loading: false, whileTesting: 'start_normal', keep: false, run: null, runWas: false, err: '' };
function bmRemDev() { return bmRemote.dev ? (bmRemote.devices.find(d => d.id === bmRemote.dev) || null) : null; }
function bmRemName() { const d = bmRemDev(); return d ? d.name : ''; }
function bmRemoteOn() { return !!(bmRemote.run && bmRemote.run.running); }
function bmRemJoin(list) { return list.length <= 1 ? (list[0] || '') : list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1]; }
function bmAgo(s) { if (s == null) return ''; if (s < 3600) return Math.max(1, Math.round(s / 60)) + ' min'; if (s < 86400) return Math.round(s / 3600) + ' h'; const d = Math.round(s / 86400); return d + ' day' + (d === 1 ? '' : 's'); }

// ---- the "Test on" list ----
function bmRemoteTestOnHtml() {
  const W = BM_WORDS.remote, cards = (bmTab && bmTab.gpus && bmTab.gpus.cards) || [], sk = (bmTab && bmTab.skyrim) || {};
  const devs = bmRemote.devices || [];
  if (!devs.length) return '';
  const one = (id, name, meta, dot, word, off) => '<label class="bm-opt" style="align-items:center;' + (off ? 'opacity:.6' : '') + '"><input type="radio" name="bmDev" value="' + esc(id) + '" ' + (bmRemote.dev === id ? 'checked ' : '') + (off ? 'disabled ' : '') +
    'onchange="bmPickDevice(\'' + jsq(id) + '\')"> <span style="flex:1"><b>' + esc(name) + '</b><span class="bm-hint" style="display:block;margin:0">' + esc(meta) + '</span></span><span>' + ovDot(dot) + esc(word) + '</span></label>';
  const c0 = cards[0];
  const here = [c0 ? c0.name : null, c0 && c0.memoryGb ? bmNum(c0.memoryGb, 1) + ' GB ' + W.vramWord : null, sk.hasGame !== false ? W.skyrimHere : null].filter(Boolean).join(' · ');
  let h = '<div class="bm-q">' + esc(W.testOn) + '</div>' + one('', W.thisPc, here, 'ok', W.thisReady, false);
  for (const d of devs) {
    if (d.state === 'offline') h += one(d.id, d.name, d.agoS != null ? W.lastSeen(bmAgo(d.agoS)) : '', 'off', W.offline, true);
    else if (d.state === 'needs_update') h += one(d.id, d.name, W.outOfDate, 'warn', W.needsUpdate, true);
    else {
      const runs = (d.runs || []).map(r => r === 'service:voice' ? W.voice : W.decisions);
      h += one(d.id, d.name, [d.card, d.vramMb ? bmNum(d.vramMb / 1024, 1) + ' GB ' + W.vramWord : null, runs.length ? W.runs(bmRemJoin(runs)) : null, d.courier ? W.courier(d.courier) : null].filter(Boolean).join(' · '), 'ok', W.ready, false);
    }
  }
  return h;
}
async function bmRemoteLoadDevices() {
  try { bmRemote.devices = (await bmGet('/config/benchmarks/remote/devices')).devices || []; } catch (e) { bmRemote.devices = []; }
  if (bmRemote.dev && !bmRemDev()) bmRemote.dev = '';
  bmSetupBuilt = ''; const s = document.getElementById('bmSetup'); if (s && bmTab) { bmSetupBuilt = 'r'; s.innerHTML = bmSetupHtml(bmTab); }
}
async function bmPickDevice(id) {
  bmRemote.dev = id; bmRemote.plan = null; bmRemote.err = '';
  bmRedrawSetup(); bmRedrawList();
  if (!id) return;
  bmRemote.loading = true; bmRedrawList();
  try {
    const p = await bmGet('/config/benchmarks/remote/plan?device=' + encodeURIComponent(id));
    if (bmRemote.dev === id) { if (p.ok) { bmRemote.plan = p; bmRemote.err = ''; } else bmRemote.err = bmRemoteWords(p); }
  } catch (e) { bmRemote.err = BM_WORDS.remote.checkFailed; }
  bmRemote.loading = false; bmRedrawSetup(); bmRedrawList();
}
function bmRedrawSetup() { const s = document.getElementById('bmSetup'); if (s && bmTab) s.innerHTML = bmSetupHtml(bmTab); bmUpdateBits(); }

// ---- the model list: where each model is, and what will happen ----
function bmRemoteHeadHtml() {
  if (!bmRemote.dev) return '';
  return '<th>' + esc(BM_WORDS.remote.onDevice(bmRemName())) + '</th><th>' + esc(BM_WORDS.remote.what) + '</th>';
}
function bmRemoteCellsHtml(r) {
  if (!bmRemote.dev) return '';
  const W = BM_WORDS.remote, e = bmRemote.plan && bmRemote.plan.models && bmRemote.plan.models[r.key];
  if (!e) return '<td colspan="2" class="lm-muted">' + esc(bmRemote.loading ? W.checking : '') + '</td>';
  const dot = e.on === 'already_there' ? 'ok' : 'off';
  const what = e.what === 'as_is' ? W.asIs : (e.what === 'too_big' ? W.tooBig(bmRemName(), bmRemote.plan.device.vramMb ? bmNum(bmRemote.plan.device.vramMb / 1024, 1) + ' GB' : '') : (e.fit === 'tight' ? W.sentTight : W.sent));
  return '<td class="bm-st">' + ovDot(dot) + esc(e.on === 'already_there' ? W.alreadyThere : W.notThere) + '</td><td class="bm-wrapok lm-muted">' + esc(what) + '</td>';
}
function bmRemoteFitDot(r) {
  const e = bmRemote.dev && bmRemote.plan && bmRemote.plan.models && bmRemote.plan.models[r.key];
  if (!e) return null;
  return bmFitDot({ fit: e.fit, fitAlongside: false, fitWhy: '', fitNeedMb: e.needMb, fitFreeMb: e.freeMb, fitSlots: e.slots, fitConfidence: e.confidence });
}
function bmRemoteSummaryHtml(rows) {
  if (!bmRemote.dev) return '';
  const W = BM_WORDS.remote, p = bmRemote.plan, d = bmRemName();
  if (!p) return bmRemote.err ? '<div class="bm-msg">' + bmWarn(bmRemote.err) + '</div>' : '';
  const ticked = (rows || []).filter(r => r.ticked), need = ticked.filter(r => (p.models[r.key] || {}).on !== 'already_there').reduce((n, r) => n + (r.sizeBytes || 0), 0);
  const secs = p.speedBps && need ? Math.round(need / p.speedBps) : null;
  let h = '<div class="bm-hint" style="margin-top:8px">' + esc(need ? W.sending(bmBytes(need), d, secs ? bmLeft(secs) : '', p.speedBps ? bmNum(p.speedBps / 1e6) + ' MB/s' : '') : W.nothingToSend(d)) + ' ' + esc(p.freeBytes != null ? W.free(d, bmBytes(p.freeBytes)) : '') + ' ' + esc(W.deleted(d)) +
    ' <label class="bm-opt" style="display:inline-flex"><input type="checkbox" id="bmKeep" ' + (bmRemote.keep ? 'checked ' : '') + 'onchange="bmRemote.keep=this.checked"> <span>' + esc(W.keep(d)) + '</span></label></div>';
  return h + '<div class="bm-hint">' + esc(W.keepNote(d)) + '</div>';
}

// ---- While testing (instead of "Skyrim and VRAM") ----
function bmRemoteWhileHtml() {
  const W = BM_WORDS.remote, p = bmRemote.plan, d = bmRemName(), sv = (p && p.services) || [];
  const names = sv.map(s => s.id === 'service:decision' ? W.quickDecision : s.name), running = sv.filter(s => s.running);
  const opt = (v, label, note) => '<label class="bm-opt"><input type="radio" name="bmWhile" value="' + v + '" ' + (bmRemote.whileTesting === v ? 'checked ' : '') + 'onchange="bmRemote.whileTesting=\'' + v + '\'"> <span><b>' + esc(label) + '</b></span></label>' +
    '<div class="bm-hint" style="margin:0 0 6px 1.6rem">' + esc(note) + '</div>';
  const mb = running.reduce((n, s) => n + (s.mb || 0), 0);
  return '<div class="bm-q">' + esc(W.whileTesting) + '</div>' +
    opt('start_normal', W.loadUsual(d), names.length ? W.loadNote(bmRemJoin(names)) : W.loadNone(d)) +
    opt('keep', W.leave, running.length ? W.leaveNote(bmRemJoin(running.map(s => s.id === 'service:decision' ? W.quickDecision : s.name)), mb ? bmGb(mb) : '') : W.leaveNone) +
    opt('stop', W.stop(d), W.stopNote);
}
function bmRemoteEstimate() {
  const W = BM_WORDS.remote, rows = (bmTab && bmTab.models || []).filter(r => r.ticked), p = bmRemote.plan;
  if (!p) return '';
  const need = rows.filter(r => (p.models[r.key] || {}).on !== 'already_there').reduce((n, r) => n + (r.sizeBytes || 0), 0);
  const send = need ? (p.speedBps ? bmLeft(Math.round(need / p.speedBps)) : bmBytes(need)) : '';
  return W.estimate(rows.length, rows.length * 12, send);          // 12 minutes a model: the standard test (warm-up, the levels, the sustained run, cool-down)
}
function bmStartRowHtml(W) {
  if (!bmRemote.dev) return '<div class="lm-statusline" style="margin:8px 0 0"><span class="lm-warn">⚠️ ' + esc(W.heavy) + '</span><button id="bmStartBtn" style="margin-left:auto" onclick="bmStart()">' + esc(W.start) + '</button></div><div class="bm-note" id="bmStartMsg"></div>';
  const R = BM_WORDS.remote, p = bmRemote.plan;
  return (p && p.hasBattery ? '<div class="lm-warn" style="margin:8px 0">⚠️ ' + esc(R.battery(bmRemName())) + '</div>' : '') +
    '<div class="lm-statusline" style="margin:8px 0 0"><span class="lm-muted" id="bmEstimate">' + esc(bmRemoteEstimate()) + '</span><button id="bmStartBtn" style="margin-left:auto" onclick="bmStart()">' + esc(R.start(bmRemName())) + '</button></div><div class="bm-note" id="bmStartMsg"></div>';
}

// ---- Start ----
function bmRemoteWords(d) {
  const W = BM_WORDS.remote, n = d.device || bmRemName() || '';
  switch (d.code) {
    case 'offline': return W.isOffline(n);
    case 'needs_update': return W.oldCourier(n);
    case 'busy_benchmark': return W.busyTest(n);
    case 'no_space': case 'no_space_device': return W.noSpace(n, bmBytes(d.have_bytes || 0), bmBytes(d.need_bytes || 0));
    case 'not_downloaded': return W.downloadFirst;
    default: return d.detail || 'Something went wrong.';
  }
}
async function bmRemoteStart(takeOver) {
  const W = BM_WORDS.setup, msg = document.getElementById('bmStartMsg');
  if (msg) msg.innerHTML = '';
  let keys = bmTickedKeys();
  if (!keys.length) { bmShowErr(W.pickModel); return; }
  if (!bmSet.levels.length) { bmShowErr(W.pickLevel); return; }
  if (!bmSet.prompts.length) { bmShowErr(W.pickPrompt); return; }
  await bmSaveNow();
  let models = ((bmTab && bmTab.models) || []).filter(r => r.ticked);
  try {
    const p = await bmGet('/config/benchmarks/remote/plan?device=' + encodeURIComponent(bmRemote.dev) + '&levels=' + bmSet.levels.join(',') + '&prompts=' + bmSet.prompts.join(','));
    const over = p.ok ? models.filter(m => (p.models[m.key] || {}).fit === 'too_big').map(m => { const e = p.models[m.key]; return { key: m.key, name: String(m.title).replace(/\.gguf$/i, ''), needMb: e.needMb, freeMb: e.freeMb, slots: e.slots }; }) : [];
    const sig = bmAckSig({ keys: keys, levels: bmSet.levels, prompts: bmSet.prompts, settings: bmPlan().settings, skyrim: 'device' }, bmRemote.dev);
    if (over.length && bmAck !== sig) {
      const ans = await bmAskFit(over, BM_WORDS.models.fitAskThere);
      if (ans === 'run') bmAck = sig;
      else if (ans === 'leave') {
        for (const m of models) if (over.some(o => o.key === m.key)) m.ticked = false;
        models = models.filter(m => m.ticked);
        bmSaveNow(); bmRedrawList();
        if (!models.length) return;
        keys = bmTickedKeys();
      } else return;
    }
  } catch (e) {}                                                       // it cannot be checked: the test is not held back for that
  const pend = bmPendingOf(models);
  if (pend.count) { if (!(await bmDownloadIds(pend.ids))) return; await bmRemoteWaitForDownloads(); }
  const body = { device: bmRemote.dev, keys: keys, setup: bmPlan(), whileTesting: bmRemote.whileTesting, keep: !!bmRemote.keep, takeOver: !!takeOver };
  let d = {};
  try { d = await lmPost('/config/benchmarks/remote/start', body); } catch (e) { bmShowErr(e.message); return; }
  if (d.ok) { bmMsg = ''; await bmRemoteLoadState(); bmShowRun(); return; }
  if (d.code === 'busy_chat' && !takeOver) { if (await ownAsk(BM_WORDS.remote.takeOverAsk(d.device || bmRemName()), BM_WORDS.remote.takeOver(d.device || bmRemName()))) return bmRemoteStart(true); return; }
  bmShowErr(bmRemoteWords(d));
  bmAck = null;
}
async function bmRemoteWaitForDownloads() {
  for (let i = 0; i < 4000; i++) { await bmLoadTab(false); if (!bmQueueOn() && !((bmTab && bmTab.models) || []).some(r => r.download && r.download.state === 'running')) return; await new Promise(res => setTimeout(res, 1500)); }
}

// ---- the live panel ----
async function bmRemoteLoadState() {
  let r = null;
  try { r = (await bmGet('/config/benchmarks/remote/state')).run; } catch (e) { return; }
  const was = bmRemote.runWas;
  bmRemote.run = r; bmRemote.runWas = !!(r && r.running);
  if (r && r.serverNowEpochS) bmSkew = r.serverNowEpochS - Date.now() / 1000;
  const box = document.getElementById('bmRun');
  if (box && !(bmStatus && bmStatus.running)) box.innerHTML = r ? bmRemoteRunHtml(r) : '';
  if (!bmClockId) bmClockId = setTimeout(bmClockLoop, 1000);
  if (r && !r.running) {
    const open = Object.values(r.runs || {}).some(x => x.state !== 'saved') || r.error;
    if (!open && was) { await bmLoadResults(); await bmLoadTab(false); }
    if (was) { bmLoadResults(); }
    if (!open) { try { await lmPost('/config/benchmarks/remote/dismiss', {}); } catch (e) {} bmRemote.run = null; if (box) box.innerHTML = ''; }
  }
  bmUpdateBits();
}
function bmRemoteStepWords(r) { return r.step ? bmStepWords({ step: r.step, engine_busy: r.engineBusy }) : BM_WORDS.run.starting; }
function bmRemoteRunHtml(r) {
  const W = BM_WORDS.remote, R = BM_WORDS.run, d = r.device.name;
  let h = '';
  if (r.running) {
    const phases = W.phases(d), cur = { preparing: 0, sending: 1, testing: 2, bringing_results_back: 3 }[r.phase] || 0;
    const total = r.stepsTotal || 0, done = Math.min(r.stepsDone || 0, total);
    const stepTxt = r.phase === 'testing' && total ? ' (' + R.step(Math.min(done + 1, total), total, '').replace(/: $/, '') + ')' : '';
    const title = r.phase === 'sending' && r.sendIndex ? W.model(r.sendIndex, r.sendTotal, String(r.sendName || '').replace(/\.gguf$/i, ''), d) : W.model(r.modelIndex || 1, r.modelsTotal || 1, String(r.modelName || '').replace(/\.gguf$/i, ''), d);
    const s = r.sending;
    const pct = r.phase === 'sending' && s && s.bytesTotal ? Math.round(100 * s.bytesDone / s.bytesTotal) : (total ? Math.round(100 * done / total) : 0);
    const line = r.phase === 'sending' && s ? '<div class="bm-step lm-muted">' + esc(W.sendLine(bmGB(s.bytesDone) + ' of ' + bmBytes(s.bytesTotal), s.speedBps ? bmNum(s.speedBps / 1e6) + ' MB/s' : '', s.secondsLeft != null ? (s.secondsLeft < 60 ? s.secondsLeft + ' s' : bmLeft(s.secondsLeft)) : '')) + '</div>' : '';
    const vramNote = (r.services || []).filter(x => x.running && x.mb).map(x => W.usingVram(x.id === 'service:decision' ? BM_WORDS.remote.quickDecision : x.name, bmGb(x.mb))).join(' · ');
    let tiles = bmTilesHtml(r.readings).replace('>VRAM</span>', '>VRAM (' + esc(d) + ')</span>');
    h += '<div class="card"><div class="bm-head"><div><div class="bm-h" style="margin:0"><b>' + esc(title) + '</b></div><div class="bm-step lm-muted" id="bmStepLine">' +
      phases.map((p, i) => i === cur ? '<b>' + esc(p) + '</b>' : esc(p)).join(' → ') + esc(stepTxt) + '</div>' + line +
      '<div class="bm-step lm-muted" id="bmClock">' + esc(bmClockText({ batch_started_epoch_s: r.batchStartedEpochS, model_started_epoch_s: r.modelStartedEpochS })) + '</div></div>' +
      '<div>' + bmBtn(BM_WORDS.setup.stop, 'bmRemoteStop()', { primary: true }) + '</div></div><div class="bm-bar"><i style="width:' + pct + '%"></i></div>' +
      '<div title="' + esc(R.readings) + '">' + tiles + '</div>' + (vramNote ? '<div class="bm-hint">' + esc(vramNote) + '</div>' : '') + '<div class="bm-hint">' + esc(W.stopHint(d)) + '</div>';
    if (r.lost) h += '<div class="lm-warn" style="margin:8px 0">⚠️ ' + esc(W.lost(d, Math.min((r.lost.stepsDone || 0) + 1, r.lost.stepsTotal || 0), r.lost.stepsTotal, r.lost.saved)) + '</div>';
    h += '</div>';
  } else {
    const msg = r.reason === 'slept' ? W.slept(d, String(r.interruptedModel || r.modelName || '').replace(/\.gguf$/i, '')) : (r.reason === 'lost_contact' ? W.lostEnded(d) : (r.message || (r.error && r.error.detail) || ''));
    if (msg && (r.jobState !== 'finished')) h += '<div class="card">' + bmWarn(msg) + '</div>';
  }
  const COLLECT = ['saved', 'waiting_to_collect', 'collecting', 'failed_to_collect', 'not_run'];
  const rows = (r.models || []).filter(m => COLLECT.indexOf(m.state) >= 0);
  if (rows.length) {
    h += '<div class="card"><h3 class="bm-h">' + esc(W.coming) + '</h3>' + rows.map(m => {
      const nm = String(m.name || '').replace(/\.gguf$/i, '');
      const x = (r.runs || {})[m.run_id] || {};
      let dot = 'off', word = '', extra = '';
      if (m.state === 'saved') { dot = 'ok'; word = W.saved; }
      else if (m.state === 'waiting_to_collect') { dot = 'warn'; word = W.waiting(d); extra = W.waitingWhy(d); }
      else if (m.state === 'collecting') { dot = 'warn'; word = W.collecting(d); }
      else if (m.state === 'failed_to_collect') { dot = 'bad'; word = W.failedCollect; extra = x.reason || ''; }
      else if (m.state === 'not_run') { dot = 'off'; word = W.notRun; }
      else return '';
      return '<div class="bm-row"><span><b>' + esc(nm) + '</b></span><span>' + ovDot(dot) + '<b>' + esc(word) + '</b>' + (extra ? ' <span class="lm-muted">' + esc(extra) + '</span>' : '') + '</span></div>';
    }).join('') + '</div>';
  }
  return h;
}
async function bmRemoteStop() { try { await lmPost('/config/benchmarks/remote/cancel', {}); } catch (e) {} await bmRemoteLoadState(); await bmLoadResults(); }

function bmResultsDevice(rows) {
  const names = Array.from(new Set((rows || []).filter(r => r.device && r.device.kind === 'courier').map(r => r.device.name || '')));
  return names.length === 1 && (rows || []).every(r => r.device && r.device.kind === 'courier') ? names[0] : '';
}
// one line for the services loaded on the device, from the manifest and never per row; only when every shown row says the same
function bmResultsServicesHtml(rows, dev) {
  if (!dev) return '';
  const seen = (rows || []).map(r => JSON.stringify(((r.services || {}).seen) || []));
  if (!seen.length || seen.some(x => x !== seen[0])) return '';
  const names = JSON.parse(seen[0]);
  return names.length ? '<div class="lm-muted" style="margin:-4px 0 8px">' + esc(BM_WORDS.remote.services(bmRemJoin(names), dev)) + '</div>' : '';
}
function bmResultFlags(r) {
  if (!r.device || r.device.kind !== 'courier') return '';
  const W = BM_WORDS.remote, n = r.device.name || '';
  return (r.hot ? '<span class="bm-small lm-warn">⚠️ ' + esc(W.hot(n)) + '</span>' : '') + (r.on_battery ? '<span class="bm-small">' + esc(W.onBattery(n)) + '</span>' : '');
}
// "Test again" on a result made on another PC: the same device, the same model, whatever "While testing" is set to now
async function bmRemoteAgain(rem) {
  const dev = rem[0].device;
  if (!bmRemote.devices.some(d => d.id === dev.id && d.state === 'ready')) { bmMsg = BM_WORDS.remote.isOffline(dev.name || ''); bmRenderResults(); return; }
  await bmPickDevice(dev.id);
  const files = rem.map(r => String(r.model));
  for (const m of (bmTab.models || [])) m.ticked = !!m.path && files.indexOf(String(m.path).split(/[\\/]/).pop()) >= 0;
  bmSaveNow(); bmRedrawList(); bmRedrawSetup();
  await bmRemoteStart(false);
}
"""
