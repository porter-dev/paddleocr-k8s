async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {}
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.status === 204 ? null : response.json();
}

export const getConfig = () => request('/api/config');
export const getHealth = () => request('/api/healthz');
export const listJobs = () => request('/api/jobs');
export const getJob = (id) => request(`/api/jobs/${id}`);
export const getPages = (id) => request(`/api/jobs/${id}/pages`);
export const deleteJob = (id) => request(`/api/jobs/${id}`, { method: 'DELETE' });
export const retryJob = (id) => request(`/api/jobs/${id}/retry`, { method: 'POST' });

export const inputPdfUrl = (id) => `/api/jobs/${id}/input.pdf`;
export const resultZipUrl = (id) => `/api/jobs/${id}/result.zip`;
export const resultMarkdownUrl = (id) => `/api/jobs/${id}/result.md`;
export const jobFileUrl = (id, key) => `/api/jobs/${id}/files/${key}`;

export async function getResultMarkdown(id) {
  const response = await fetch(resultMarkdownUrl(id));
  if (!response.ok) throw new Error(`${response.status}: could not load result`);
  return response.text();
}

export function createJob({ file, chunkPages, concurrency, restructure }, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append('pdf', file);
    body.append('chunkPages', String(chunkPages));
    body.append('concurrency', String(concurrency));
    body.append('restructure', String(restructure));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve(JSON.parse(xhr.responseText));
      let detail = xhr.statusText;
      try {
        detail = JSON.parse(xhr.responseText).detail ?? detail;
      } catch {}
      reject(new Error(`${xhr.status}: ${detail}`));
    };
    xhr.onerror = () => reject(new Error('upload failed'));
    xhr.send(body);
  });
}
