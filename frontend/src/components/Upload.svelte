<script>
  import { createJob } from '../lib/api.js';
  import { bytes } from '../lib/format.js';

  let { config, onsubmitted } = $props();

  let file = $state(null);
  let chunkPages = $state(50);
  let concurrency = $state(2);
  let restructure = $state(true);
  let dragging = $state(false);
  let uploading = $state(false);
  let progress = $state(0);
  let error = $state('');
  let defaultsApplied = false;
  let input;

  $effect(() => {
    if (config && !defaultsApplied) {
      chunkPages = config.defaultChunkPages;
      concurrency = config.defaultConcurrency;
      restructure = config.defaultRestructure;
      defaultsApplied = true;
    }
  });

  const requests = $derived(file && pagesHint ? null : null);
  let pagesHint = $state(null);

  function pick(candidate) {
    error = '';
    if (!candidate) return;
    if (candidate.type !== 'application/pdf' && !candidate.name.toLowerCase().endsWith('.pdf')) {
      error = 'Only PDF files are accepted.';
      return;
    }
    file = candidate;
  }

  function onDrop(event) {
    event.preventDefault();
    dragging = false;
    pick(event.dataTransfer.files[0]);
  }

  async function submit(event) {
    event.preventDefault();
    if (!file || uploading) return;
    uploading = true;
    progress = 0;
    error = '';
    try {
      const job = await createJob({ file, chunkPages, concurrency, restructure }, (p) => (progress = p));
      file = null;
      if (input) input.value = '';
      onsubmitted?.(job);
    } catch (e) {
      error = e.message;
    } finally {
      uploading = false;
    }
  }
</script>

<form class="upload" onsubmit={submit}>
  <div
    class="drop"
    class:dragging
    class:has-file={!!file}
    role="button"
    tabindex="0"
    onclick={() => input.click()}
    onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && input.click()}
    ondragover={(e) => { e.preventDefault(); dragging = true; }}
    ondragleave={() => (dragging = false)}
    ondrop={onDrop}
  >
    <input bind:this={input} type="file" accept="application/pdf,.pdf" hidden onchange={(e) => pick(e.target.files[0])} />
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M12 18v-6" /><path d="m9 15 3-3 3 3" />
    </svg>
    {#if file}
      <div class="file">
        <strong>{file.name}</strong>
        <span class="muted">{bytes(file.size)} · click to change</span>
      </div>
    {:else}
      <div class="file">
        <strong>Drop a PDF here or click to browse</strong>
        <span class="muted">Each document becomes one job; pages are recognised in parallel requests.</span>
      </div>
    {/if}
  </div>

  <div class="options">
    <label>
      <span>Pages per request</span>
      <input type="number" min="1" max="500" bind:value={chunkPages} />
    </label>
    <label>
      <span>Requests in flight</span>
      <input type="number" min="1" max="64" bind:value={concurrency} />
    </label>
    <label class="toggle">
      <input type="checkbox" bind:checked={restructure} />
      <span>Merge tables and headings across pages</span>
    </label>
    <span class="spacer"></span>
    <button class="btn primary" type="submit" disabled={!file || uploading}>
      {#if uploading}
        Uploading {Math.round(progress * 100)}%
      {:else}
        Run OCR
      {/if}
    </button>
  </div>
  {#if uploading}<div class="bar"><div style={`width:${progress * 100}%`}></div></div>{/if}
  {#if error}<p class="error">{error}</p>{/if}
</form>

<style>
  .upload {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: var(--shadow);
    padding: 14px;
  }
  .drop {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 22px 20px;
    border: 1.5px dashed var(--line-strong);
    border-radius: var(--radius);
    background: var(--surface-2);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
  }
  .drop:hover, .drop.dragging { border-color: var(--accent); background: var(--accent-soft); }
  .drop.has-file { border-style: solid; border-color: var(--accent); }
  .drop svg { width: 34px; height: 34px; color: var(--accent); flex: none; }
  .file { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .file strong { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .options { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; padding: 14px 4px 2px; }
  .options label { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--ink-2); }
  .options input[type="number"] {
    width: 64px;
    padding: 5px 8px;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius);
    background: var(--surface);
    font-family: var(--mono);
  }
  .toggle input { accent-color: var(--accent); }
  .spacer { flex: 1; }
  .bar { height: 3px; background: var(--line); border-radius: 2px; margin-top: 10px; overflow: hidden; }
  .bar div { height: 100%; background: var(--accent); transition: width 0.2s; }
  @media (max-width: 720px) {
    .drop { padding: 16px; }
    .options { gap: 10px; }
  }
</style>
