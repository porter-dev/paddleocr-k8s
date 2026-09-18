<script>
  import { onMount } from 'svelte';
  import { duration } from '../lib/format.js';

  let { job } = $props();

  const STAGES = [
    { key: 'lease_wait', label: 'waiting for another pod', color: 'var(--line)' },
    { key: 'split', label: 'split PDF', color: 'var(--line-strong)' },
    { key: 'upload', label: 'upload chunk', color: 'var(--muted)' },
    { key: 'batch_wait', label: 'waiting for a batch', color: 'var(--line)' },
    { key: 'ocr', label: 'OCR round trip', color: 'var(--accent)' },
    { key: 'store', label: 'store pages', color: 'var(--done)' },
  ];
  const OCR_STAGE = STAGES.find((s) => s.key === 'ocr');
  const SPANS = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800].map((s) => s * 1000);

  const t = (iso) => (iso ? new Date(iso).getTime() : null);
  const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–');

  let now = $state(Date.now());
  onMount(() => {
    const tick = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(tick);
  });

  const live = $derived(!job.finished_at);
  const origin = $derived(t(job.created_at));
  const end = $derived(t(job.finished_at) ?? now);
  // A finished job gets an exact axis. A running one gets a rounded span with headroom that
  // only steps up occasionally, so bars grow to the right instead of the whole chart rescaling.
  const span = $derived.by(() => {
    const elapsed = Math.max(1000, end - origin);
    if (!live) return elapsed;
    return SPANS.find((s) => s >= elapsed * 1.15) ?? elapsed * 1.15;
  });
  const pct = (ms) => `${Math.min(100, Math.max(0, (ms / span) * 100))}%`;

  // Stages run in order inside a chunk, so each stage occupies [start + earlier stages, + its
  // own time]. The stage after the last recorded one is the one in progress right now.
  function segments(chunk, start, stop) {
    const out = [];
    let cursor = start;
    for (const stage of STAGES) {
      const seconds = chunk.timings?.[stage.key];
      if (seconds == null) continue;
      out.push({ from: cursor, to: cursor + seconds * 1000, color: stage.color, label: `${stage.label}: ${duration(seconds)}` });
      cursor += seconds * 1000;
    }
    if (chunk.status === 'running' && stop > cursor) {
      const next = STAGES.find((st) => chunk.timings?.[st.key] == null && st.key !== 'lease_wait') ?? OCR_STAGE;
      out.push({ from: cursor, to: stop, color: next.color, label: `${next.label}: ${duration((stop - cursor) / 1000)} so far`, active: true });
    } else if (stop > cursor + 500) {
      out.push({ from: cursor, to: stop, color: 'transparent', label: `other: ${duration((stop - cursor) / 1000)}` });
    }
    return out;
  }

  const rows = $derived(
    job.chunks.map((c) => {
      const start = t(c.started_at);
      const stop = t(c.finished_at) ?? (start ? now : null);
      const total = start && stop ? (stop - start) / 1000 : null;
      const earlier = (c.history ?? []).filter((a) => a.outcome && a.outcome !== 'done' && t(a.started_at) !== start);
      const pages = c.last_page - c.first_page + 1;
      const ocrRate = c.timings?.ocr ? pages / c.timings.ocr : null;
      return { chunk: c, start, stop, total, earlier, pages, ocrRate, segments: start ? segments(c, start, stop) : [] };
    })
  );

  const recognition = $derived.by(() => {
    const starts = rows.flatMap((r) => [r.start, ...(r.chunk.history ?? []).map((a) => t(a.started_at))]).filter(Boolean);
    const stops = rows.map((r) => r.stop).filter(Boolean);
    return starts.length ? { start: Math.min(...starts), stop: Math.max(...stops) } : null;
  });

  // Service throughput: pages recognised over the time at least one request was at the OCR
  // service. Summing round trips would double-count time when requests overlap.
  const ocrTotals = $derived.by(() => {
    const done = rows.filter((r) => r.chunk.timings?.ocr && r.start);
    const intervals = done
      .map((r) => {
        const tm = r.chunk.timings;
        const from = r.start + ((tm.lease_wait ?? 0) + (tm.split ?? 0) + (tm.upload ?? 0) + (tm.batch_wait ?? 0)) * 1000;
        return [from, from + tm.ocr * 1000];
      })
      .sort((a, b) => a[0] - b[0]);
    let covered = 0;
    let cursor = -Infinity;
    for (const [from, to] of intervals) {
      if (to <= cursor) continue;
      covered += to - Math.max(from, cursor);
      cursor = to;
    }
    const pages = done.reduce((sum, r) => sum + r.pages, 0);
    return { pages, seconds: covered / 1000, rate: covered ? pages / (covered / 1000) : null, requests: done.length };
  });

  const phases = $derived.by(() => {
    const list = [{ label: 'Queued before start', seconds: job.metrics.queued_seconds, from: origin, to: t(job.started_at) }];
    if (recognition) list.push({ label: live ? 'Recognition so far' : 'Recognition (all requests)', seconds: (recognition.stop - recognition.start) / 1000, from: recognition.start, to: recognition.stop });
    if (job.timings?.assemble != null) list.push({ label: job.assembled_with === 'restructure-pages' ? 'Restructure pages' : 'Concatenate pages', seconds: job.timings.assemble });
    if (job.timings?.archive != null) list.push({ label: 'Write markdown, page index and zip', seconds: job.timings.archive });
    list.push({ label: live ? 'Elapsed so far' : 'Total (submitted → finished)', seconds: (end - origin) / 1000, from: origin, to: end });
    if (ocrTotals.rate) list.push({ label: `OCR throughput (${ocrTotals.pages} pages ÷ ${duration(ocrTotals.seconds)} with a request at the service)`, rate: ocrTotals.rate, seconds: ocrTotals.seconds });
    return list;
  });

  const ticks = $derived.by(() => {
    const seconds = span / 1000;
    const step = seconds <= 60 ? 10 : seconds <= 300 ? 30 : seconds <= 900 ? 60 : seconds <= 3600 ? 300 : 900;
    const out = [];
    for (let s = 0; s <= seconds + 0.001; s += step) out.push(s);
    return out;
  });
</script>

<section class="timing">
  <div class="gantt">
    <div class="axis">
      {#each ticks as s, i}
        <span class:last={i === ticks.length - 1 && s * 1000 >= span * 0.97} style={`left:${pct(s * 1000)}`}>{duration(s)}</span>
      {/each}
      {#if live}<span class="now" style={`left:${pct(now - origin)}`}></span>{/if}
    </div>
    {#each rows as row (row.chunk.index)}
      <div class="row">
        <div class="label mono">#{row.chunk.index + 1} <span class="muted">p{row.chunk.first_page}–{row.chunk.last_page}</span></div>
        <div class="track">
          {#each row.earlier as attempt}
            <div class={`seg ghost ${attempt.outcome}`} style={`left:${pct(t(attempt.started_at) - origin)};width:${pct(Math.max((t(attempt.finished_at) ?? now) - t(attempt.started_at), 800))}`} title={`${attempt.outcome} attempt on ${attempt.owner}: ${clock(attempt.started_at)} → ${clock(attempt.finished_at)}`}></div>
          {/each}
          {#if row.chunk.status === 'failed' && row.start}
            <div class="seg failed" style={`left:${pct(row.start - origin)};width:${pct(Math.max(row.stop - row.start, 800))}`} title={row.chunk.error ?? 'failed'}></div>
          {:else}
            {#each row.segments as seg}
              <div class="seg" class:active={seg.active} style={`left:${pct(seg.from - origin)};width:${pct(Math.max(seg.to - seg.from, seg.active ? 800 : 0))};background:${seg.color}`} title={`${seg.label} · ${row.chunk.owner ?? ''}`}></div>
            {/each}
          {/if}
        </div>
      </div>
    {/each}
    <div class="legend">
      {#each STAGES as stage}
        <span><i style={`background:${stage.color}`}></i>{stage.label}</span>
      {/each}
      <span><i class="failed"></i>failed</span>
      <span><i class="ghost"></i>abandoned attempt (pod restarted)</span>
    </div>
  </div>

  <div class="tables">
    <table>
      <thead>
        <tr><th>Request</th><th>Pages</th><th>Pod</th><th>Started</th><th>Ended</th><th class="num">Split</th><th class="num">Upload</th><th class="num">OCR</th><th class="num" title="pages ÷ OCR round trip: sent to the gateway until its response arrived">Pages/s</th><th class="num">Store</th><th class="num">Total</th><th class="num">Attempts</th><th>Status</th></tr>
      </thead>
      <tbody>
        {#each rows as row (row.chunk.index)}
          <tr>
            <td class="mono">#{row.chunk.index + 1}</td>
            <td class="mono">{row.chunk.first_page}–{row.chunk.last_page}</td>
            <td class="mono pod" title={row.chunk.owner ?? ''}>{row.chunk.owner ? row.chunk.owner.replace(/-\d+$/, '') : '–'}</td>
            <td class="mono">{clock(row.chunk.started_at)}</td>
            <td class="mono">{clock(row.chunk.finished_at)}</td>
            <td class="num mono">{row.chunk.timings?.split != null ? duration(row.chunk.timings.split) : '–'}</td>
            <td class="num mono">{row.chunk.timings?.upload != null ? duration(row.chunk.timings.upload) : '–'}</td>
            <td class="num mono">{row.chunk.timings?.ocr != null ? duration(row.chunk.timings.ocr) : '–'}</td>
            <td class="num mono rate">{row.ocrRate ? row.ocrRate.toFixed(2) : '–'}</td>
            <td class="num mono">{row.chunk.timings?.store != null ? duration(row.chunk.timings.store) : '–'}</td>
            <td class="num mono">{duration(row.total)}</td>
            <td class="num mono">{row.chunk.attempts}</td>
            <td>
              <span class={`pill ${row.chunk.status}`}>{row.chunk.status}</span>
              {#each row.earlier as attempt}
                <div class="earlier muted" title={`${attempt.outcome} attempt on ${attempt.owner}`}>{attempt.outcome} on {attempt.owner.replace(/-\d+$/, '')}, {clock(attempt.started_at)}</div>
              {/each}
              {#if row.chunk.error}<div class="error">{row.chunk.error}</div>{/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    <table class="phases">
      <thead><tr><th>Phase</th><th>From</th><th>To</th><th class="num">Duration</th></tr></thead>
      <tbody>
        {#each phases as phase}
          <tr>
            <td>{phase.label}</td>
            <td class="mono">{phase.from ? clock(new Date(phase.from).toISOString()) : '–'}</td>
            <td class="mono">{phase.to ? clock(new Date(phase.to).toISOString()) : '–'}</td>
            <td class="num mono">{phase.rate ? `${phase.rate.toFixed(2)} pages/s` : duration(phase.seconds)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<style>
  .timing { padding: 14px 28px 18px; background: var(--surface); border-bottom: 1px solid var(--line); }
  .gantt { display: flex; flex-direction: column; gap: 4px; }
  .axis { position: relative; height: 18px; margin-left: 132px; border-bottom: 1px solid var(--line); font-size: 11px; color: var(--muted); }
  .axis span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .axis span:first-child { transform: none; }
  .axis span.last { transform: translateX(-100%); }
  .axis .now { width: 1px; height: 6px; bottom: 0; background: var(--running); transform: none; }
  .row { display: grid; grid-template-columns: 132px 1fr; align-items: center; }
  .label { font-size: 12px; padding-right: 10px; white-space: nowrap; }
  .track { position: relative; height: 18px; background: repeating-linear-gradient(90deg, transparent 0 calc(10% - 1px), var(--line) calc(10% - 1px) 10%); border-radius: 3px; overflow: hidden; }
  .seg { position: absolute; top: 2px; bottom: 2px; }
  .seg:first-child { border-radius: 3px 0 0 3px; }
  .seg.active {
    border-radius: 0 3px 3px 0;
    background-image: repeating-linear-gradient(-45deg, rgba(255, 255, 255, 0.28) 0 6px, transparent 6px 12px);
    background-size: 17px 17px;
    animation: crawl 0.8s linear infinite;
  }
  @keyframes crawl { to { background-position: 17px 0; } }
  @media (prefers-reduced-motion: reduce) { .seg.active { animation: none; } }
  .seg.failed { background: var(--failed); border-radius: 3px; }
  .seg.ghost { background: repeating-linear-gradient(135deg, var(--line-strong) 0 4px, transparent 4px 8px); border-radius: 3px; }
  .seg.ghost.failed { background: repeating-linear-gradient(135deg, var(--failed) 0 4px, transparent 4px 8px); }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 8px 0 0 132px; font-size: 11px; color: var(--muted); }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
  .legend i.failed { background: var(--failed); }
  .legend i.ghost { background: repeating-linear-gradient(135deg, var(--line-strong) 0 3px, transparent 3px 6px); }
  .pod { max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .earlier { font-size: 11px; margin-top: 2px; }
  .rate { color: var(--accent); font-weight: 500; }
  .tables { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-top: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { padding: 5px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
  th { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 500; }
  .num { text-align: right; }
  .error { font-size: 11px; }
  @media (max-width: 1000px) {
    .tables { grid-template-columns: 1fr; }
    .timing { padding: 14px 16px; overflow-x: auto; }
  }
</style>
