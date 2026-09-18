<script>
  import { renderMarkdown } from '../lib/markdown.js';

  let { jobId, source } = $props();
  const html = $derived(renderMarkdown(jobId, source));
</script>

<div class="md">{@html html}</div>

<style>
  .md { max-width: 74ch; line-height: 1.65; font-size: 14.5px; }
  .md :global(h1) { font-size: 22px; margin: 0 0 14px; }
  .md :global(h2) { font-size: 18px; margin: 26px 0 10px; }
  .md :global(h3) { font-size: 15.5px; margin: 20px 0 8px; }
  .md :global(p) { margin: 0 0 12px; }
  .md :global(img) { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 4px; margin: 6px 0; }
  .md :global(table) { border-collapse: collapse; margin: 12px 0; font-size: 13px; display: block; overflow-x: auto; }
  .md :global(th), .md :global(td) { border: 1px solid var(--line-strong); padding: 5px 9px; text-align: left; vertical-align: top; }
  .md :global(th) { background: var(--surface-2); }
  .md :global(code) { font-family: var(--mono); font-size: 12.5px; background: var(--surface-2); padding: 1px 4px; border-radius: 3px; }
  .md :global(pre) { background: var(--surface-2); padding: 12px; border-radius: var(--radius); overflow-x: auto; }
  .md :global(blockquote) { margin: 0 0 12px; padding-left: 12px; border-left: 3px solid var(--line-strong); color: var(--ink-2); }
</style>
