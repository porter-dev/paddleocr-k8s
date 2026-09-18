<script>
  import { onMount } from 'svelte';
  import PdfViewer from './PdfViewer.svelte';
  import Markdown from './Markdown.svelte';
  import TimingPanel from './TimingPanel.svelte';
  import { getJob, getPages, getResultMarkdown, deleteJob, retryJob, inputPdfUrl, resultZipUrl, resultMarkdownUrl } from '../lib/api.js';
  import { navigate } from '../lib/router.svelte.js';
  import { duration, bytes, when, statusLabel } from '../lib/format.js';

  let { id, onchanged } = $props();

  let job = $state(null);
  let error = $state('');
  let page = $state(1);
  let tab = $state('page');
  let pages = $state(null);
  let document = $state('');
  let showTiming = $state(false);

  const done = $derived(job?.status === 'done');
  const active = $derived(job && ['queued', 'running', 'assembling'].includes(job.status));
  const pageMarkdown = $derived(pages?.[page - 1]?.markdown ?? '');
  const chunkOf = $derived(job?.chunks.find((c) => page >= c.first_page && page <= c.last_page));
  const maxChunkSeconds = $derived(job ? Math.max(1, ...job.chunks.map((c) => c.seconds ?? 0)) : 1);

  async function refresh() {
    try {
      job = await getJob(id);
      error = '';
    } catch (e) {
      error = e.message;
    }
  }

  onMount(() => {
    refresh();
    const timer = setInterval(() => (active || job?.status === 'failed') && refresh(), 2000);
    return () => clearInterval(timer);
  });

  $effect(() => {
    if (done && pages === null) {
      getPages(id).then((p) => (pages = p)).catch((e) => (error = e.message));
      getResultMarkdown(id).then((m) => (document = m)).catch((e) => (error = e.message));
    }
  });

  async function remove() {
    if (!confirm(`Delete "${job.file_name}" with its input, images and results?`)) return;
    try {
      await deleteJob(id);
      onchanged?.();
      navigate('/');
    } catch (e) {
      error = e.message;
    }
  }

  async function retry() {
    try {
      job = await retryJob(id);
      onchanged?.();
    } catch (e) {
      error = e.message;
    }
  }

  function jumpToChunk(chunk) {
    page = chunk.first_page;
    tab = 'page';
  }
</script>

<main class="job">
  {#if error}<p class="error banner">{error}</p>{/if}
  {#if job}
    <header class="head">
      <a class="back" href="#/">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m15 6-6 6 6 6" /></svg>All documents
      </a>
      <div class="title">
        <h2 title={job.file_name}>{job.file_name}</h2>
        <span class={`pill ${job.status}`}>{statusLabel(job)}</span>
      </div>
      <div class="actions">
        <a class="btn" href={inputPdfUrl(job.id)} download={job.file_name}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>Input PDF
        </a>
        <a class="btn primary" href={resultZipUrl(job.id)} aria-disabled={!done}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>Output zip
        </a>
        <a class="btn" href={resultMarkdownUrl(job.id)} aria-disabled={!done} target="_blank" rel="noopener">Markdown</a>
        {#if job.status === 'failed'}
          <button class="btn retry" type="button" onclick={retry} title="Re-queue the failed requests; finished ones are kept">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /></svg>Retry
          </button>
        {/if}
        <button class="btn" class:on={showTiming} type="button" onclick={() => (showTiming = !showTiming)} aria-expanded={showTiming} title="Per-request and per-stage timing">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2.5 2.5" /><path d="M9 2h6" /></svg>Timing
        </button>
        <button class="btn quiet" type="button" onclick={remove} disabled={active} title="Delete this job">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h16" /><path d="M10 11v6" /><path d="M14 11v6" /><path d="M6 7l1 13h10l1-13" /><path d="M9 7V4h6v3" /></svg>
        </button>
      </div>
    </header>

    <section class="summary">
      <dl class="facts">
        <div><dt>Submitted</dt><dd class="mono">{when(job.created_at)}</dd></div>
        <div><dt>Waited</dt><dd class="mono">{duration(job.metrics.queued_seconds)}</dd></div>
        <div><dt>{job.finished_at ? 'Took' : 'Running for'}</dt><dd class="mono">{duration(job.metrics.elapsed_seconds)}</dd></div>
        <div><dt>Pages</dt><dd class="mono">{job.metrics.pages_done} / {job.pages}</dd></div>
        <div><dt>Throughput</dt><dd class="mono">{job.metrics.pages_per_second.toFixed(2)} pages/s</dd></div>
        <div><dt>Requests</dt><dd class="mono">{job.chunks.length} × {job.chunk_pages} pages, {job.concurrency} in flight</dd></div>
        <div><dt>Size</dt><dd class="mono">{bytes(job.bytes)}</dd></div>
        <div><dt>Assembly</dt><dd>{job.assembled_with ?? (job.restructure ? 'merge tables and headings' : 'concatenate pages')}</dd></div>
      </dl>
      <div class="chunks" aria-label="requests">
        {#each job.chunks as chunk (chunk.index)}
          <button
            type="button"
            class={`chunk ${chunk.status}`}
            class:current={chunkOf?.index === chunk.index}
            style={`--h:${chunk.seconds ? Math.max(0.15, chunk.seconds / maxChunkSeconds) : 0.15}`}
            title={`request ${chunk.index + 1}: pages ${chunk.first_page}–${chunk.last_page}, ${chunk.status}${chunk.seconds != null ? `, ${duration(chunk.seconds)}` : ''}${chunk.attempts > 1 ? `, ${chunk.attempts} attempts` : ''}${chunk.error ? `\n${chunk.error}` : ''}`}
            onclick={() => jumpToChunk(chunk)}
          ><span></span></button>
        {/each}
        <span class="legend muted">one bar per request · height is time taken · click to jump to its first page</span>
      </div>
      {#if job.error}<p class="error">{job.error}</p>{/if}
    </section>

    {#if showTiming}
      <TimingPanel {job} />
    {/if}

    <section class="panes">
      <div class="pane">
        <div class="pane-head">
          <span>Input</span>
          <span class="muted">{job.file_name}</span>
        </div>
        <PdfViewer url={inputPdfUrl(job.id)} bind:page />
      </div>
      <div class="pane">
        <div class="pane-head tabs">
          <button type="button" class:active={tab === 'page'} onclick={() => (tab = 'page')}>Page {page}</button>
          <button type="button" class:active={tab === 'document'} onclick={() => (tab = 'document')}>Full document</button>
          <button type="button" class:active={tab === 'raw'} onclick={() => (tab = 'raw')}>Raw markdown</button>
        </div>
        <div class="pane-body">
          {#if !done}
            <div class="placeholder">
              {#if job.status === 'failed'}Recognition failed, so there is no output for this document.{:else}Output appears here when recognition finishes.{/if}
            </div>
          {:else if tab === 'page'}
            {#if pages === null}
              <div class="placeholder">Loading…</div>
            {:else if pageMarkdown.trim()}
              <Markdown jobId={job.id} source={pageMarkdown} />
            {:else}
              <div class="placeholder">Page {page} produced no text.</div>
            {/if}
          {:else if tab === 'document'}
            {#if !document}
              <div class="placeholder">Loading…</div>
            {:else}
              <Markdown jobId={job.id} source={document} />
            {/if}
          {:else}
            <pre class="raw">{document}</pre>
          {/if}
        </div>
      </div>
    </section>
  {:else if !error}
    <p class="muted banner">Loading…</p>
  {/if}
</main>

<style>
  .job { display: flex; flex-direction: column; min-height: calc(100vh - 52px); }
  .banner { padding: 16px 28px; }
  .head {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 14px 28px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .back { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; color: var(--muted); text-decoration: none; white-space: nowrap; }
  .back:hover { color: var(--accent); }
  .back svg { width: 16px; height: 16px; }
  .title { display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1; }
  .title h2 { font-size: 17px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .actions { display: flex; gap: 8px; }
  .actions .btn.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
  .actions .retry { border-color: var(--failed); color: var(--failed); }
  .summary { padding: 14px 28px; background: var(--surface); border-bottom: 1px solid var(--line); }
  .facts { display: flex; flex-wrap: wrap; gap: 6px 32px; margin: 0; }
  .facts div { display: flex; flex-direction: column; }
  dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  dd { margin: 0; font-size: 14px; }
  .chunks { display: flex; align-items: flex-end; gap: 3px; height: 36px; margin-top: 12px; max-width: 100%; }
  .chunk { flex: 0 1 28px; min-width: 6px; height: 100%; padding: 0; border: 0; background: transparent; display: flex; align-items: flex-end; cursor: pointer; }
  .chunk span { display: block; width: 100%; height: calc(var(--h) * 100%); border-radius: 2px 2px 0 0; background: var(--line-strong); transition: height 0.3s; }
  .chunk.running span { background: var(--running); }
  .chunk.done span { background: var(--done); }
  .chunk.failed span { background: var(--failed); }
  .chunk.current span { outline: 2px solid var(--accent); outline-offset: 1px; }
  .chunk:hover span { filter: brightness(1.15); }
  .legend { font-size: 11px; margin-left: 12px; align-self: center; }
  .panes { display: grid; grid-template-columns: 1fr 1fr; flex: 1; min-height: 520px; background: var(--surface); }
  .pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .pane + .pane { border-left: 1px solid var(--line); }
  .pane-head {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 40px;
    padding: 0 14px;
    font-size: 12px;
    font-weight: 500;
    border-bottom: 1px solid var(--line);
    background: var(--surface-2);
    flex: none;
  }
  .pane-head .muted { font-weight: 400; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tabs { gap: 2px; }
  .tabs button { height: 28px; padding: 0 12px; border: 0; border-radius: var(--radius); background: transparent; color: var(--muted); cursor: pointer; }
  .tabs button.active { background: var(--surface); color: var(--ink); box-shadow: 0 0 0 1px var(--line); }
  .pane-body { flex: 1; overflow: auto; padding: 22px 30px; min-height: 0; }
  .placeholder { color: var(--muted); padding: 30px 0; text-align: center; }
  .raw { margin: 0; font-family: var(--mono); font-size: 12px; white-space: pre-wrap; word-break: break-word; }
  @media (max-width: 900px) {
    .head { flex-wrap: wrap; }
    .panes { grid-template-columns: 1fr; }
    .pane { height: 70vh; }
    .pane + .pane { border-left: 0; border-top: 1px solid var(--line); }
  }
</style>
