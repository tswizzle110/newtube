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
export function trackWatch(w) {
  // Heartbeats for one session are merged: latest position/duration win, intervals accumulate
  // (the server unions them, so re-sent intervals are harmless).
  const prev = q.watch.get(w.session_id)
  if (prev) w = { ...w, intervals: [...prev.intervals, ...w.intervals], ended: prev.ended || w.ended }
  q.watch.set(w.session_id, w)
  schedule()
}

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
// Things that must record their final state before the page goes away (e.g. a WatchTracker
// saving the current position). They run first, then everything queued is sent as one beacon.
const unloadHooks = new Set()
export const onUnload = (fn) => { unloadHooks.add(fn); return () => unloadHooks.delete(fn) }
function leaving() {
  for (const fn of unloadHooks) { try { fn() } catch { /* never block unload */ } }
  flushBeacon()
}
addEventListener('pagehide', leaving)
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leaving() })

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
