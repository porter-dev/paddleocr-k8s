import { Marked } from 'marked';
import DOMPurify from 'dompurify';
import { jobFileUrl } from './api.js';

const RELATIVE = /^(?!(?:[a-z]+:|\/|#|data:))/i;

DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
  if (node.tagName === 'IMG' && data.attrName === 'src' && RELATIVE.test(data.attrValue)) {
    data.attrValue = jobFileUrl(currentJobId, data.attrValue);
  }
});

let currentJobId = '';

export function renderMarkdown(jobId, markdown) {
  currentJobId = jobId;
  const marked = new Marked({
    gfm: true,
    renderer: {
      image({ href, title, text }) {
        const src = /^(https?:)?\/\//.test(href) ? href : jobFileUrl(jobId, href);
        return `<img src="${src}" alt="${escapeAttr(text ?? '')}" title="${escapeAttr(title ?? '')}" loading="lazy">`;
      },
    },
  });
  return DOMPurify.sanitize(marked.parse(markdown), { ADD_ATTR: ['loading', 'width'] });
}

function escapeAttr(value) {
  return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}
