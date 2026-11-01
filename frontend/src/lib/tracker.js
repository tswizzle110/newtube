// Local-only behaviour logging. Batches impressions, clicks, signals and watch
// heartbeats and posts them to our own server (/api/events), where they go into SQLite.

const FLUSH_MS = 5000
const q = { impressions: [], clicks: [], events: [], watch: new Map() }
let timer = null

// crypto.randomUUID() needs HTTPS/localhost; getRandomValues works on plain-http LAN too.
export function uid() {
  const b = crypto.getRandomValues(new Uint8Array(12))
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
}

function schedule() {
  if (!timer) timer = setTimeout(() => { timer = null; flush() }, FLUSH_MS)
}

export function trackImpression(i) { q.impressions.push(i); schedule() }
export function trackClick(c) { q.clicks.push(c); schedule() }
export function trackEvent(type, fields = {}) { q.events.push({ type, ...fields }); schedule() }
export function trackWatch(w) { q.watch.set(w.session_id, w); schedule() } // latest state wins

function drain() {
  const batch = {
    impressions: q.impressions, clicks: q.clicks, events: q.events, watch: [...q.watch.values()],
  }
  if (!batch.impressions.length && !batch.clicks.length && !batch.events.length && !batch.watch.length) {
    return null
  }
  q.impressions = []; q.clicks = []; q.events = []; q.watch = new Map()
  return batch
}

export async function flush() {
  const batch = drain()
  if (!batch) return
  try {
    const r = await fetch('/api/events', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(batch), keepalive: true,
    })
    if (!r.ok && r.status !== 400 && r.status !== 422) throw new Error(String(r.status))
  } catch {
    // Network blip: put it back and try again on the next flush.
    q.impressions.unshift(...batch.impressions); q.clicks.unshift(...batch.clicks)
    q.events.unshift(...batch.events)
    for (const w of batch.watch) if (!q.watch.has(w.session_id)) q.watch.set(w.session_id, w)
    schedule()
  }
}

function flushBeacon() {
  const batch = drain()
  if (batch) navigator.sendBeacon('/api/events', new Blob([JSON.stringify(batch)], { type: 'application/json' }))
}
addEventListener('pagehide', flushBeacon)
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushBeacon() })

// Svelte action: report a card as an impression once >=50% visible for >=1s.
// If it stays visible longer, the longer time is reported when it scrolls away.
export function impression(node, params) {
  let p = params, since = 0, timer = null, reported = false

  const report = (ms) => {
    reported = true
    trackImpression({ video_id: p.videoId, surface: p.surface, position: p.position,
      request_id: p.requestId, visible_ms: Math.round(ms) })
  }
  const stop = () => {
    clearTimeout(timer)
    if (since && reported) report(performance.now() - since)
    since = 0
  }
  const io = new IntersectionObserver(([e]) => {
    if (e.intersectionRatio >= 0.5) {
      if (!since) {
        since = performance.now()
        timer = setTimeout(() => report(1000), 1000)
      }
    } else if (since) {
      const ms = performance.now() - since
      clearTimeout(timer)
      if (ms >= 1000) report(ms)
      since = 0
    }
  }, { threshold: [0, 0.5, 1] })
  io.observe(node)

  return {
    update(next) { p = next },
    destroy() { io.disconnect(); stop() },
  }
}
