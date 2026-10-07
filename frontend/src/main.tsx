import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js').catch(() => undefined);
  }, { once: true });
}

// A deploy wipes old hashed chunks (kept one generation deep by
// scripts/keep-assets.mjs, but not forever). If a lazy route's chunk is gone,
// vite dispatches this event and the route never mounts — the app shows the
// shell chrome with nothing inside. One reload picks up the fresh index and
// its new hashes; the flag stops an offline client from reload-looping.
window.addEventListener('vite:preloadError', () => {
  if (!sessionStorage.getItem('td:chunkReloaded')) {
    sessionStorage.setItem('td:chunkReloaded', '1');
    window.location.reload();
  }
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
