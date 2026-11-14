// Small preferences kept in this browser.
function read(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v === '1' } catch { return fallback }
}
export const prefs = $state({
  autoplay: read('autoplay', true),     // when a video ends, go on to the next one (queue, playlist, then Up next)
})
export function setAutoplay(on) {
  prefs.autoplay = on
  try { localStorage.setItem('autoplay', on ? '1' : '0') } catch { /* private mode */ }
}
