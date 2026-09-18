<script>
  import { onMount } from 'svelte';
  import Upload from './components/Upload.svelte';
  import JobCards from './components/JobCards.svelte';
  import JobPage from './components/JobPage.svelte';
  import { route, navigate } from './lib/router.svelte.js';
  import { getConfig, getHealth, listJobs } from './lib/api.js';

  let config = $state(null);
  let health = $state(null);
  let jobs = $state([]);
  let listError = $state('');

  const jobMatch = $derived(route.path.match(/^\/jobs\/([a-z0-9]+)$/));
  const anyActive = $derived(jobs.some((j) => ['queued', 'running', 'assembling'].includes(j.status)));

  async function refresh() {
    try {
      jobs = await listJobs();
      listError = '';
    } catch (e) {
      listError = e.message;
    }
  }

  async function refreshHealth() {
    try {
      health = await getHealth();
    } catch {
      health = { ok: false, ocrReady: false };
    }
  }

  onMount(() => {
    getConfig().then((c) => (config = c)).catch(() => {});
    refresh();
    refreshHealth();
    let timer;
    const schedule = () => {
      timer = setTimeout(async () => {
        if (!jobMatch) await refresh();
        schedule();
      }, anyActive ? 2000 : 10000);
    };
    schedule();
    const healthTimer = setInterval(refreshHealth, 30000);
    return () => {
      clearTimeout(timer);
      clearInterval(healthTimer);
    };
  });

  $effect(() => {
    route.path;
    refresh();
  });

  function submitted(job) {
    jobs = [job, ...jobs];
    navigate(`/jobs/${job.id}`);
  }
</script>

<header class="topbar">
  <a class="brand" href="#/">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>
    <h1>Document OCR</h1>
  </a>
  {#if jobMatch}
    <span class="crumb muted">/ {jobs.find((j) => j.id === jobMatch[1])?.file_name ?? jobMatch[1]}</span>
  {/if}
  <span class="spacer"></span>
  {#if health}
    <span class="health"><span class={`dot ${health.ocrReady ? 'ok' : 'bad'}`}></span>PaddleOCR {health.ocrReady ? 'ready' : 'unreachable'}</span>
  {/if}
  {#if config}
    <span class="health"><span class="dot ok"></span><span class="target">{config.storage}</span></span>
  {/if}
</header>

{#if jobMatch}
  <JobPage id={jobMatch[1]} onchanged={refresh} />
{:else}
  <main class="page">
    <Upload {config} onsubmitted={submitted} />
    <div class="section-head">
      <h2>Documents</h2>
      <span class="muted">{jobs.length} {jobs.length === 1 ? 'job' : 'jobs'}</span>
    </div>
    {#if listError}<p class="error">{listError}</p>{/if}
    <JobCards {jobs} onchanged={refresh} />
  </main>
{/if}

<style>
  .brand { display: flex; align-items: center; gap: 10px; color: inherit; text-decoration: none; }
  .brand svg { width: 20px; height: 20px; color: var(--accent); }
  .crumb { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
