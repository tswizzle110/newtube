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

// Everything is stamped with when it HAPPENED (this browser's clock; the server corrects for a wrong clock), because
// a report may be delivered much later: laptop closed, wifi switched, tab killed.
const now = () => Date.now() / 1000
export function trackImpression(i) { q.impressions.push({ ...i, ts: now() }); schedule() }
export function trackClick(c) { q.clicks.push({ ...c, ts: now() }); schedule() }
export function trackEvent(type, fields = {}) { q.events.push({ type, ...fields, ts: now() }); schedule() }
export function trackWatch(w) {
  // Heartbeats for one session are merged: latest position/duration win, intervals accumulate
  // (the server unions them, so re-sent intervals are harmless). The first sighting fixes when the viewing began.
  const prev = q.watch.get(w.session_id)
  w = prev
    ? { ...w, intervals: [...prev.intervals, ...w.intervals], ended: prev.ended || w.ended, started_at: prev.started_at }
    : { ...w, started_at: startedAt.get(w.session_id) ?? now() }
  startedAt.set(w.session_id, w.started_at)
  q.watch.set(w.session_id, w)
  schedule()
}
const startedAt = new Map()

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

// ---- the outbox ------------------------------------------------------------------------------------------
// A drained batch is first PUT AWAY (kept in memory and, one key per batch, in localStorage) and only removed once
// the server has confirmed it. So nothing is lost if the connection is down, the laptop sleeps, or the tab is closed
// mid-send: the batches are sent again, from this tab or the next one. Each carries an id, so a batch whose
// confirmation was lost on the way is recognised by the server and counted once.
const PREFIX = 'nt_outbox:'
const MAX_BATCHES = 150              // beyond this, the oldest are dropped (a very long offline stretch)
const MAX_AGE_MS = 30 * 24 * 3600 * 1000
const outbox = new Map()             // batch_id -> batch, oldest first
let sending = false, failures = 0, retryTimer = null

function persist(b) { try { localStorage.setItem(PREFIX + b.batch_id, JSON.stringify(b)) } catch { /* private mode / full */ } }
function forget(id) { outbox.delete(id); try { localStorage.removeItem(PREFIX + id) } catch { /* ignore */ } }

/** Pick up batches an earlier page left behind (closed or crashed while offline). */
function recover() {
  try {
    const found = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith(PREFIX)) found.push(k)
    }
    for (const k of found) {
      try {
        const b = JSON.parse(localStorage.getItem(k))
        if (!b?.batch_id || Date.now() - (b.created ?? 0) > MAX_AGE_MS) localStorage.removeItem(k)
        else if (!outbox.has(b.batch_id)) outbox.set(b.batch_id, b)
      } catch { localStorage.removeItem(k) }
    }
    const sorted = [...outbox.values()].sort((x, y) => (x.created ?? 0) - (y.created ?? 0))
    outbox.clear()
    for (const b of sorted) outbox.set(b.batch_id, b)
  } catch { /* no storage: in-memory only */ }
}

function seal() {
  const b = drain()
  if (!b) return null
  b.batch_id = uid() + uid()
  b.created = Date.now()
  outbox.set(b.batch_id, b)
  persist(b)
  while (outbox.size > MAX_BATCHES) forget(outbox.keys().next().value)
  return b
}

export async function flush() {
  seal()
  await sendOutbox()
}

async function sendOutbox() {
  if (sending || !outbox.size) return
  sending = true
  clearTimeout(retryTimer)
  try {
    for (const [id, b] of [...outbox]) {
      const { created, ...body } = b
      const r = await fetch('/api/events', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ...body, sent_at: now() }),
      })
      // Delivered, or refused as malformed (resending would never help): either way it is done with.
      // Anything else (login expired, server restarting, 5xx) is kept and tried again.
      if (r.ok || r.status === 400 || r.status === 413 || r.status === 422) { forget(id); failures = 0 }
      else throw new Error(String(r.status))
    }
  } catch {
    failures++
    // 2 s, 4 s, 8 s ... up to a minute, with jitter; and any sign of life (online, tab visible) tries at once.
    retryTimer = setTimeout(() => { retryTimer = null; sendOutbox() }, Math.min(60_000, 2000 * 2 ** Math.min(failures, 5)) * (0.75 + Math.random() * 0.5))
  } finally {
    sending = false
  }
}
/** Something suggests the network may be back (online, tab shown, page restored): try now rather than later. */
function retryNow() { failures = 0; flush() }
/** For tests and diagnostics: how many batches are waiting to be delivered. */
export const pending = () => outbox.size

function flushBeacon() {
  // The page is going away: last chance to hand things to the browser. The batch stays in the outbox (and in
  // localStorage) until a real request is confirmed, so if this beacon never arrives, the next page sends it again.
  const b = seal()
  if (b) {
    const { created, ...body } = b
    try { navigator.sendBeacon('/api/events', new Blob([JSON.stringify({ ...body, sent_at: now() })], { type: 'application/json' })) } catch { /* the outbox has it */ }
  }
}
// Things that must record their final state before the page goes away (e.g. a WatchTracker
// saving the current position). They run first, then everything queued is sent as one beacon.
const unloadHooks = new Set()
export const onUnload = (fn) => { unloadHooks.add(fn); return () => unloadHooks.delete(fn) }
function leaving() {
  for (const fn of unloadHooks) { try { fn() } catch { /* never block unload */ } }
  flushBeacon()
}
addEventListener('pagehide', leaving)
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leaving(); else retryNow() })
addEventListener('online', retryNow)           // wifi back / switched
addEventListener('pageshow', retryNow)         // restored from the back-forward cache
recover()                                      // anything an earlier page could not deliver
setTimeout(() => flush(), 1500)

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


// Follows a <video> element and reports which stretches of the video were actually played,
// plus pauses, seeks and speed changes. The server turns this into watched fraction / resume point.
export class WatchTracker {
  constructor(video, { videoId, source }) {
    this.v = video; this.videoId = videoId; this.source = source
    this.sessionId = uid()
    this.open = null          // start of the interval currently being played
    this.lastT = 0            // last time reported by timeupdate (before any seek)
    this.seekFrom = null
    this.closed = []
    this.started = false
    this.on = {
      playing: () => {
        if (this.open === null) this.open = this.v.currentTime
        if (!this.started) { this.started = true; this.beat() }
        this.event('play', { position: this.v.currentTime })
      },
      pause: () => {
        if (this.v.ended) return
        this.close(this.v.currentTime)
        this.event('pause', { position: this.v.currentTime })
      },
      ended: () => { this.close(this.v.currentTime); this.beat(true) },
      timeupdate: () => { if (!this.v.seeking) this.lastT = this.v.currentTime },
      seeking: () => {
        this.close(this.lastT)
        if (this.seekFrom === null) this.seekFrom = this.lastT
      },
      seeked: () => {
        if (this.seekFrom !== null && Math.abs(this.v.currentTime - this.seekFrom) > 0.5) {
          this.event('seek', { from: Math.round(this.seekFrom), to: Math.round(this.v.currentTime) })
        }
        this.seekFrom = null
        if (!this.v.paused) this.open = this.v.currentTime
      },
    }
    for (const [k, fn] of Object.entries(this.on)) this.v.addEventListener(k, fn)
    this.timer = setInterval(() => this.heartbeat(), 10000)
    this.unhook = onUnload(() => this.heartbeat())   // tab closed / reloaded / hidden
  }

  /** Called by the player for deliberate actions (Shaka also changes playbackRate internally,
   *  e.g. 0 while buffering, so we can't infer user intent from the element's ratechange). */
  event(type, payload) {
    trackEvent(type, { video_id: this.videoId, session_id: this.sessionId, payload })
  }

  close(end) {
    if (this.open !== null && end > this.open) this.closed.push([this.open, end])
    this.open = null
  }

  heartbeat() {
    if (this.open !== null) { this.close(this.v.currentTime); this.open = this.v.currentTime }
    if (this.started) this.beat()
  }

  beat(ended = false) {
    const d = Number.isFinite(this.v.duration) ? this.v.duration : undefined
    trackWatch({ session_id: this.sessionId, video_id: this.videoId, source: this.source,
      position: this.v.currentTime || 0, duration: d, intervals: this.closed, ended })
    this.closed = []
  }

  /** Call when leaving the page. */
  stop() {
    clearInterval(this.timer); this.unhook()
    for (const [k, fn] of Object.entries(this.on)) this.v.removeEventListener(k, fn)
    this.close(this.v.currentTime)
    if (this.started) { this.beat(true); flush() }
  }
}
