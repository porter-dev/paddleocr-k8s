export function duration(seconds) {
  if (seconds == null) return '–';
  if (seconds < 60) return Number.isInteger(seconds) ? `${seconds}s` : `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export function bytes(n) {
  if (!n) return '';
  if (n < 1 << 20) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1 << 20)).toFixed(1)} MB`;
}

export function when(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function statusLabel(job) {
  switch (job.status) {
    case 'queued':
      return 'Queued';
    case 'running':
      return `Recognising ${job.metrics.pages_done}/${job.pages}`;
    case 'assembling':
      return 'Assembling';
    case 'done':
      return 'Done';
    case 'failed':
      return 'Failed';
    default:
      return job.status;
  }
}
