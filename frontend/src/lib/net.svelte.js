// What the connection to the server is doing, for the banner and for refreshing pages that failed to load.
export const net = $state({
  offline: !navigator.onLine,   // the browser knows it has no network
  unreachable: false,           // it thinks it has one, but the server is not answering (restarting, wrong wifi...)
  failed: false,                // some page load gave up: reload the page once the connection is back
  epoch: 0,                     // bumped to make the current page load itself again
})

export function refreshPages() {
  net.failed = false
  net.epoch += 1
}

addEventListener('online', () => {
  net.offline = false
  if (net.failed) refreshPages()
})
addEventListener('offline', () => { net.offline = true })

// A laptop that slept for a long while: whatever the page shows (the feed, the lists) is stale, so load it again.
const STALE_AFTER_MS = 30 * 60 * 1000
let hiddenAt = 0
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') hiddenAt = Date.now()
  else {
    if (hiddenAt && Date.now() - hiddenAt > STALE_AFTER_MS) refreshPages()
    hiddenAt = 0
  }
})

// While something is wrong, look again every few seconds (cheap public request) so the banner clears and failed pages
// reload as soon as the server answers, even if the browser never reports 'online' (it thought it was online all along).
setInterval(async () => {
  if (!net.unreachable && !net.failed) return
  try {
    await fetch('/api/auth/status', { cache: 'no-store' })
    net.unreachable = false
    if (net.failed) refreshPages()
  } catch {
    net.unreachable = navigator.onLine
  }
}, 5000)

/** Resolves when the browser reports it is online again (or after `ms`, to look again). */
export const whenOnline = (ms = 30_000) => new Promise((resolve) => {
  addEventListener('online', resolve, { once: true })
  setTimeout(resolve, ms)
})
