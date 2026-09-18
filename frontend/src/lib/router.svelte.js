function read() {
  const hash = location.hash.replace(/^#/, '');
  return hash.startsWith('/') ? hash : '/';
}

export const route = $state({ path: read() });

window.addEventListener('hashchange', () => {
  route.path = read();
});

export function navigate(path) {
  location.hash = '#' + path;
}
