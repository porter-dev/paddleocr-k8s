<script>
  import { onMount } from 'svelte';
  import * as pdfjs from 'pdfjs-dist';
  import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

  let { url, page = $bindable(1) } = $props();

  let doc = $state(null);
  let total = $state(0);
  let zoom = $state('fit');
  let error = $state('');
  let loading = $state(true);
  let stage;
  let viewport;
  let renderTask = null;
  let renderChain = Promise.resolve();
  let generation = 0;
  let pageInput = $state('1');
  let rendered = $state(false);

  onMount(() => {
    let cancelled = false;
    pdfjs
      .getDocument({ url })
      .promise.then((d) => {
        if (cancelled) return;
        doc = d;
        total = d.numPages;
        loading = false;
      })
      .catch((e) => {
        error = `Could not open the PDF: ${e.message}`;
        loading = false;
      });
    const observer = new ResizeObserver(() => render());
    observer.observe(viewport);
    return () => {
      cancelled = true;
      observer.disconnect();
      renderTask?.cancel();
      doc?.destroy();
    };
  });

  $effect(() => {
    if (doc && page) {
      pageInput = String(page);
      render();
    }
    zoom;
  });

  // pdf.js refuses overlapping render() calls on one canvas, so every render draws into a
  // fresh canvas that replaces the displayed one when it finishes; renders are also queued
  // so a request superseded by a newer page or zoom is skipped when its turn comes.
  function render() {
    const mine = ++generation;
    renderTask?.cancel();
    renderChain = renderChain
      .then(() => (mine === generation ? renderNow() : undefined))
      .catch(() => {});
  }

  async function renderNow() {
    if (!doc || !stage || !viewport) return;
    const mine = generation;
    const clamped = Math.min(Math.max(1, page), total);
    if (clamped !== page) {
      page = clamped;
      return;
    }
    const pdfPage = await doc.getPage(page);
    const base = pdfPage.getViewport({ scale: 1 });
    const available = viewport.clientWidth - 32;
    const scale = zoom === 'fit' ? available / base.width : Number(zoom);
    const ratio = window.devicePixelRatio || 1;
    const view = pdfPage.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(view.width * ratio);
    canvas.height = Math.floor(view.height * ratio);
    canvas.style.width = `${Math.floor(view.width)}px`;
    canvas.style.height = `${Math.floor(view.height)}px`;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const task = pdfPage.render({ canvasContext: ctx, viewport: view });
    renderTask = task;
    try {
      await task.promise;
      if (mine === generation) {
        stage.replaceChildren(canvas);
        rendered = true;
        error = '';
      }
    } catch (e) {
      if (e?.name !== 'RenderingCancelledException') error = e.message;
    } finally {
      if (renderTask === task) renderTask = null;
    }
  }

  function go(delta) {
    page = Math.min(Math.max(1, page + delta), total || 1);
  }

  function commitPageInput() {
    const n = parseInt(pageInput, 10);
    if (Number.isFinite(n)) page = Math.min(Math.max(1, n), total || 1);
    else pageInput = String(page);
  }

  function onKey(event) {
    if (event.key === 'ArrowRight' || event.key === 'PageDown') (event.preventDefault(), go(1));
    if (event.key === 'ArrowLeft' || event.key === 'PageUp') (event.preventDefault(), go(-1));
  }
</script>

<div class="viewer" tabindex="0" onkeydown={onKey} role="region" aria-label="PDF viewer">
  <div class="toolbar">
    <button type="button" class="btn small" onclick={() => go(-1)} disabled={page <= 1} aria-label="previous page">‹</button>
    <span class="pager mono">
      <input type="text" inputmode="numeric" bind:value={pageInput} onchange={commitPageInput} onkeydown={(e) => e.key === 'Enter' && commitPageInput()} aria-label="page number" />
      / {total || '…'}
    </span>
    <button type="button" class="btn small" onclick={() => go(1)} disabled={page >= total} aria-label="next page">›</button>
    <span class="spacer"></span>
    <select bind:value={zoom} aria-label="zoom">
      <option value="fit">Fit width</option>
      <option value="0.75">75%</option>
      <option value="1">100%</option>
      <option value="1.5">150%</option>
      <option value="2">200%</option>
    </select>
  </div>
  <div class="canvas-wrap" bind:this={viewport}>
    {#if loading}<div class="note">Loading PDF…</div>{/if}
    {#if error}<div class="note error">{error}</div>{/if}
    <div class="stage" bind:this={stage} class:hidden={loading || error || !rendered}></div>
  </div>
</div>

<style>
  .viewer { display: flex; flex-direction: column; flex: 1; min-height: 0; outline: none; }
  .viewer .canvas-wrap { min-height: 0; }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 40px;
    padding: 0 10px;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
  }
  .pager { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--muted); }
  .pager input {
    width: 44px;
    padding: 3px 6px;
    text-align: right;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius);
    background: var(--surface);
    font-family: var(--mono);
    font-size: 12px;
  }
  .spacer { flex: 1; }
  select { padding: 4px 6px; border: 1px solid var(--line-strong); border-radius: var(--radius); background: var(--surface); font-size: 12px; }
  .canvas-wrap { flex: 1; overflow: auto; background: var(--ground); padding: 16px; display: flex; justify-content: center; align-items: flex-start; }
  .stage :global(canvas) { display: block; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25), 0 8px 24px rgba(0, 0, 0, 0.12); background: white; }
  .stage.hidden { display: none; }
  .note { color: var(--muted); padding: 40px; }
</style>
