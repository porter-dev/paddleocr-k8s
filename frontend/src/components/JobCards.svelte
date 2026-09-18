<script>
  import { deleteJob, retryJob, inputPdfUrl, resultZipUrl, resultMarkdownUrl } from '../lib/api.js';
  import { navigate } from '../lib/router.svelte.js';
  import { duration, bytes, when, statusLabel } from '../lib/format.js';

  let { jobs, onchanged } = $props();
  let error = $state('');

  async function remove(job) {
    if (!confirm(`Delete "${job.file_name}" with its input, images and results?`)) return;
    try {
      await deleteJob(job.id);
      onchanged?.();
    } catch (e) {
      error = e.message;
    }
  }

  async function retry(job) {
    try {
      await retryJob(job.id);
      onchanged?.();
    } catch (e) {
      error = e.message;
    }
  }

  const stop = (e) => e.stopPropagation();
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if jobs.length === 0}
  <div class="empty">No documents yet. Upload a PDF above to start.</div>
{:else}
  <div class="cards">
    {#each jobs as job (job.id)}
      {@const done = job.status === 'done'}
      {@const active = ['queued', 'running', 'assembling'].includes(job.status)}
      <article
        class={`card ${job.status}`}
        role="link"
        tabindex="0"
        onclick={() => navigate(`/jobs/${job.id}`)}
        onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), navigate(`/jobs/${job.id}`))}
      >
        <div class="top">
          <h3 title={job.file_name}>{job.file_name}</h3>
          <span class={`pill ${job.status}`}>{statusLabel(job)}</span>
        </div>
        <div class="meta muted mono">{job.id} · {bytes(job.bytes)} · submitted {when(job.created_at)}</div>
        {#if job.status === 'running'}
          <div class="bar"><div style={`width:${(job.metrics.pages_done / job.pages) * 100}%`}></div></div>
        {/if}
        <dl class="facts">
          <div><dt>Pages</dt><dd class="mono">{job.pages}</dd></div>
          <div><dt>{job.finished_at ? 'Took' : 'Running for'}</dt><dd class="mono" title={`waited ${duration(job.metrics.queued_seconds)} in the queue first`}>{duration(job.metrics.elapsed_seconds)}</dd></div>
          <div><dt>Pages/s</dt><dd class="mono">{job.metrics.pages_per_second ? job.metrics.pages_per_second.toFixed(2) : '–'}</dd></div>
          <div><dt>Requests</dt><dd class="mono">{job.metrics.chunks_done}/{job.chunks.length}</dd></div>
        </dl>
        {#if job.error}<p class="error">{job.error}</p>{/if}
        <div class="actions" onclick={stop} onkeydown={stop} role="group" aria-label="downloads">
          <a class="btn small" href={inputPdfUrl(job.id)} download={job.file_name} title="Download the submitted PDF">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>Input PDF
          </a>
          <a class="btn small" href={resultZipUrl(job.id)} aria-disabled={!done} title="Download markdown and images as a zip">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>Output zip
          </a>
          <a class="btn small" href={resultMarkdownUrl(job.id)} aria-disabled={!done} target="_blank" rel="noopener" title="Open the markdown in a new tab">Markdown</a>
          {#if job.status === 'failed'}
            <button class="btn small retry" type="button" onclick={() => retry(job)} title="Re-queue the failed requests; finished ones are kept">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5" /></svg>Retry
            </button>
          {/if}
          <span class="spacer"></span>
          <button class="btn small quiet" type="button" onclick={() => remove(job)} disabled={active} title="Delete this job">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h16" /><path d="M10 11v6" /><path d="M14 11v6" /><path d="M6 7l1 13h10l1-13" /><path d="M9 7V4h6v3" /></svg>
          </button>
        </div>
      </article>
    {/each}
  </div>
{/if}

<style>
  .empty {
    padding: 40px;
    text-align: center;
    color: var(--muted);
    background: var(--surface);
    border: 1px dashed var(--line-strong);
    border-radius: 8px;
  }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; }
  .card {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px 18px 14px 22px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: var(--shadow);
    cursor: pointer;
    transition: border-color 0.15s, transform 0.15s;
    overflow: hidden;
  }
  .card::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--line-strong); }
  .card.running::before, .card.assembling::before { background: var(--running); }
  .card.done::before { background: var(--done); }
  .card.failed::before { background: var(--failed); }
  .card:hover { border-color: var(--accent); transform: translateY(-1px); }
  .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .top h3 { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .meta { font-size: 11px; }
  .bar { height: 4px; background: var(--line); border-radius: 2px; overflow: hidden; }
  .bar div { height: 100%; background: var(--running); transition: width 0.4s; }
  .facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 4px 0 2px; }
  .facts div { display: flex; flex-direction: column; }
  dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
  dd { margin: 0; font-size: 15px; }
  .actions { display: flex; align-items: center; gap: 6px; padding-top: 8px; border-top: 1px solid var(--line); }
  .retry { border-color: var(--failed); color: var(--failed); }
  .spacer { flex: 1; }
  @media (max-width: 480px) {
    .cards { grid-template-columns: 1fr; }
    .facts { grid-template-columns: repeat(2, 1fr); }
  }
</style>
