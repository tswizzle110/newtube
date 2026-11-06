<script>
  import { onMount } from 'svelte'
  import { duration as fmt } from '../lib/format.js'
  import { WatchTracker, trackEvent } from '../lib/tracker.js'
  import { COLORS, resolveActions, usable } from '../lib/sponsorblock.js'
  import { api as http } from '../lib/api.js'   // (this component has its own prop called `api`)

  // video: metadata from /api/video/<id>.  startAt: resume position (s).  api: bindable handle.
  // sb: { data: {segments, full_labels}, settings, channelId } once SponsorBlock info has loaded.
  let { video, startAt = 0, source = 'direct', api = $bindable(), onended = null, sb = null, theater = false, ontheater = null, compact = false, keys = true } = $props()

  let box, el
  // Touch: the first tap on a playing video only brings the controls back; the next tap pauses. (A tap is followed
  // by an emulated mouse move that wakes the controls BEFORE the click arrives, so what matters is whether they
  // were hidden when the finger went down.)
  let tapRevealsOnly = false, lastPointer = 'mouse'
  const fingerDown = (e) => { lastPointer = e.pointerType; tapRevealsOnly = e.pointerType === 'touch' && !showControls }
  function videoClick() {
    if (tapRevealsOnly) { tapRevealsOnly = false; wake(); return }
    toggle()
  }
  let paused = $state(true)
  let t = $state(0)
  let dur = $state(video.duration ?? 0)
  let buffered = $state(0)
  let volume = $state(loadVolume())
  let muted = $state(false)
  let rate = $state(1)
  let showControls = $state(true)
  let fullscreen = $state(false)
  let menu = $state(null) // 'quality' | 'speed' | 'captions' | null
  let mode = $state('loading') // 'loading' | 'dash' | 'muxed' | 'error'
  let error = $state('')
  let qualities = $state([]) // [{ h: height, fps }], high to low
  let chosenQuality = $state('auto')
  let activeHeight = $state(0)
  let capLang = $state(null)
  let audioLang = $state(initialAudio()) // 'original' or a language code like 'es-US'
  let hover = $state(null) // { frac, time }
  let scrubbing = $state(false)
  let toast = $state('')
  let help = $state(false)

  // ---- connection trouble (wifi switched, laptop slept, server restarting) --------------------------------
  let netNote = $state('')       // shown while the picture is stuck for lack of a connection
  let gaveUp = $state(false)     // many attempts failed: offer a button instead of retrying forever
  let destroyed = false
  const STUCK_AFTER = 6000       // ms of "wants to play, cannot" before we step in (shorter stalls are ordinary buffering)
  const GIVE_UP_AFTER = 12       // automatic attempts (about three minutes) before asking you
  let codecUsed = null           // 'avc1' once the browser could not decode the first choice
  let recovering = false, recoverTries = 0
  let lastT = -1, stuckFor = 0, lastTick = Date.now(), watchdog = null
  // A video left paused for a long while (a background tab, a laptop that slept): the browser may have dropped its
  // decoder or buffers, and the stream links may have gone stale. The sound can then play with a frozen picture, or
  // playback can fail with "connection" trouble until the page is reopened.
  const STALE_PAUSE_MS = 10 * 60 * 1000
  const FROZEN_AFTER = 3000      // ms of sound/time moving with no new picture before we step in
  let pausedAt = 0, refreshing = false
  let lastFrameAt = 0, frozenTries = 0, vfc = 0

  // ---- SponsorBlock state
  let sbOn = $state(true)        // quick per-video off switch
  let sbButton = $state(null)    // segment currently offered as "Skip ..." button
  let sbToast = $state(null)     // { seg } after an automatic skip
  const handled = new Set()      // segments already skipped (re-armed if you rewind past them)
  const ignored = new Set()      // segments you undid: never auto-skipped again on this page
  let lastSkip = null
  let sbMarkStart = $state(null) // seconds, set with [
  let sbDraft = $state(null)     // segment being submitted: { start, end, category, busy, error }
  let poiDone = false
  let sbTimer = null
  let mutedBySb = false, prevMuted = false

  const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3]
  const audioTracks = $derived(video.audio_tracks ?? [])
  const chapters = $derived(video.chapters ?? [])
  const total = $derived(dur || video.duration || 0)
  const segments = $derived(
    chapters.length
      ? chapters.map((c, i) => ({ title: c.title, start: c.start, end: chapters[i + 1]?.start ?? total }))
      : [{ title: '', start: 0, end: total }],
  )
  const sbActions = $derived(sb?.settings ? resolveActions(sb.settings, sb.channelId) : {})
  const sbSegments = $derived(
    sb?.data && sb.settings && sbOn ? usable(sb.data.segments, sb.settings, sbActions, total) : [],
  )
  const sbLabels = $derived(sb?.settings?.category_labels ?? {})
  const sbPoi = $derived(sbSegments.find((s) => s.action === 'poi') ?? null)
  const sbPoiButton = $derived(sbPoi && sbActions[sbPoi.category] === 'button' && t < sbPoi.start - 1 ? sbPoi : null)
  const currentChapter = $derived(chapters.findLast?.((c) => c.start <= t) ?? null)

  let shakaPlayer = null
  let tracker = null
  let hideTimer = null
  let toastTimer = null
  let sbToastTimer = null

  // Videos can carry machine-dubbed audio in ~20 languages. Default is always the original.
  function initialAudio() {
    let v = 'original'
    try { v = localStorage.getItem('audioLang') || 'original' } catch { /* ignore */ }
    return v !== 'original' && video.audio_tracks?.some((a) => a.lang === v) ? v : 'original'
  }
  const manifestUrl = (lang, codec = null) => {
    const q = []
    if (lang && lang !== 'original') q.push(`lang=${encodeURIComponent(lang)}`)
    if (codec) q.push(`codec=${codec}`)
    return `/api/dash/${video.id}.mpd` + (q.length ? '?' + q.join('&') : '')
  }
  // "1080p60", "1440p (2K)", "2160p (4K)"
  const qname = (h, fps = 0) => `${h}p${fps >= 50 ? Math.round(fps) : ''}${h >= 2160 ? ' (4K)' : h >= 1440 ? ' (2K)' : ''}`

  async function setAudio(lang) {
    menu = null
    if (lang === audioLang || !shakaPlayer) return
    audioLang = lang
    try { localStorage.setItem('audioLang', lang) } catch { /* ignore */ }
    const pos = el.currentTime, wasPlaying = !el.paused
    try {
      await shakaPlayer.load(manifestUrl(lang), pos) // same position, new audio track
      syncQualities()
      if (chosenQuality !== 'auto') setQuality(chosenQuality)
      if (wasPlaying) el.play().catch(() => {})
      const tr = audioTracks.find((a) => a.lang === lang || (lang === 'original' && a.original))
      say(`Audio: ${tr?.name ?? lang}`)
    } catch (e) {
      say('Could not switch audio')
      console.warn(e)
    }
  }

  function loadVolume() {
    try { return Number(localStorage.getItem('volume') ?? 1) } catch { return 1 }
  }
  function say(msg) {
    toast = msg
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => (toast = ''), 1400)
  }

  // ---- startup -----------------------------------------------------------------------------
  onMount(() => {
    let gone = false
    tracker = new WatchTracker(el, { videoId: video.id, source })
    sbTimer = setInterval(sbTick, 100)
    ;(async () => {
      try {
        // Lazy chunk: only the watch page pays for the player library (served from this server).
        const shaka = (await import('shaka-player/dist/shaka-player.dash.js')).default
        shaka.polyfill.installAll()
        if (!shaka.Player.isBrowserSupported()) throw new Error('MSE not supported')
        shakaPlayer = new shaka.Player()
        await shakaPlayer.attach(el)
        shakaPlayer.configure({
          // Start playing after ~1 s of buffer, and start at a quality that suits a home connection
          // (Shaka's default assumes 1 Mbit/s and ramps up from the lowest rung). ABR still adapts.
          streaming: {
            bufferingGoal: 90, rebufferingGoal: 1, bufferBehind: 30,
            // A dropped connection must not end playback: retry each segment for a good while (1, 2, 4, 8... s), and when
            // Shaka gives up on one, tell the watchdog below to step in rather than leaving the picture frozen.
            retryParameters: { maxAttempts: 6, baseDelay: 1000, backoffFactor: 2, fuzzFactor: 0.5, timeout: 30_000, stallTimeout: 8000, connectionTimeout: 10_000 },
            failureCallback: (err) => { console.warn('streaming failure:', err?.code); stuckFor = Math.max(stuckFor, STUCK_AFTER - 1000) },
          },
          abr: { enabled: true, defaultBandwidthEstimate: 25_000_000 },
          // (The manifest keeps Shaka's default of two tries: a 404 there means "no adaptive streams, use the plain file",
          // which must not wait. A dropped connection while loading is handled by loadManifest below.)
          manifest: { dash: { ignoreMinBufferTime: true } },
        })
        // Errors that used to vanish into the console: say so, and (for broken media pipelines) rebuild right away.
        shakaPlayer.addEventListener('error', (ev) => {
          const e = ev.detail
          console.warn('player error:', e?.category, e?.code)
          trackEvent('player_error', { video_id: video.id, payload: { category: e?.category, code: e?.code } })
          if (e?.category === 3 && !recovering) stuckFor = STUCK_AFTER      // 3 = media: the pipeline itself broke
          else stuckFor = Math.max(stuckFor, STUCK_AFTER - 1000)
        })
        shakaPlayer.addEventListener('variantchanged', syncQualities)
        shakaPlayer.addEventListener('adaptation', syncQualities)
        const from = startAt > 5 ? startAt : null
        try {
          await loadManifest(from)
        } catch (e) {
          // Not "no DASH for this video" (HTTP error): maybe this browser can't decode the chosen codec
          // (VP9/AV1). Ask for the most compatible one before giving up on DASH.
          if (e?.code === 1001) throw e
          console.warn('DASH load failed, retrying with H.264:', e?.code ?? e)
          codecUsed = 'avc1'
          await loadManifest(from)
        }
        if (gone) return
        netNote = ''
        mode = 'dash'
        syncQualities()
        el.play().catch(() => {})
      } catch (e) {
        if (gone) return
        if (e?.code === 1001 && e.data?.[1] === 429) botBlocked()      // YouTube's bot check: say so, do not try other ways
        else fallback(e)
      }
    })()
    watchdog = setInterval(watch, 1000)
    // Every picture actually shown calls this; if it stops while time moves, the picture is frozen (see watch()).
    const frame = () => { lastFrameAt = Date.now(); vfc = el.requestVideoFrameCallback(frame) }
    if (el.requestVideoFrameCallback) vfc = el.requestVideoFrameCallback(frame)
    const visible = () => { lastFrameAt = Date.now(); frozenTries = 0 }
    document.addEventListener('visibilitychange', visible)
    return () => {
      document.removeEventListener('visibilitychange', visible)
      try { el.cancelVideoFrameCallback?.(vfc) } catch { /* ignore */ }
      gone = true
      destroyed = true
      clearInterval(watchdog)
      tracker?.stop()
      shakaPlayer?.destroy()
      clearTimeout(hideTimer); clearTimeout(toastTimer); clearTimeout(sbToastTimer); clearInterval(sbTimer)
    }
  })

  // ---- recovery ----------------------------------------------------------------------------------------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const whenOnline = () => new Promise((r) => { addEventListener('online', r, { once: true }); setTimeout(r, 20_000) })
  // "We could not reach the server" (as opposed to "the server said no"): worth trying again.
  function transient(e) {
    if (e?.category !== 1) return false                         // 1 = network
    if (e.code !== 1001) return true                            // 1001 = the server answered with an error status...
    const status = e.data?.[1]
    return status === 0 || status >= 500                        // ...which only matters if it can pass (a proxy hiccup, a restart).
    // (429 is NOT here: that is YouTube's bot check, and asking again only makes it worse. See botBlocked.)
  }
  async function loadManifest(from) {
    for (let attempt = 0; ; attempt++) {
      try {
        await shakaPlayer.load(manifestUrl(audioLang, codecUsed), from)
        return
      } catch (e) {
        if (destroyed || !transient(e) || attempt >= 6) throw e
        netNote = navigator.onLine ? 'Connection problem: trying again…' : 'Offline: will start when the connection is back'
        if (navigator.onLine) await sleep(1000 * 2 ** attempt)
        else await whenOnline()                                 // do not use up attempts while there is no network at all
      }
    }
  }

  // Did playback actually move on within `ms`?
  async function progressed(ms) {
    const from = el.currentTime
    for (let waited = 0; waited < ms && !destroyed; waited += 250) {
      await sleep(250)
      if (el.ended || (el.readyState >= 3 && el.currentTime > from + 0.3)) return true
    }
    return false
  }

  // Once a second: is the picture stuck although you want it to play? (Covers every cause at once: a wifi switch,
  // a laptop that slept, a restarted server, a stream URL that expired.) Time spent frozen while the laptop slept
  // counts as already stuck, so recovery starts the moment it wakes up.
  function watch() {
    const t = Date.now(), gap = t - lastTick
    lastTick = t
    if (destroyed || (mode !== 'dash' && mode !== 'muxed') || !el) return
    const advanced = Math.abs(el.currentTime - lastT) > 0.01
    lastT = el.currentTime
    if (advanced && !el.paused && !el.seeking && !recovering && !refreshing && document.visibilityState === 'visible'
        && el.requestVideoFrameCallback && el.videoWidth > 0 && lastFrameAt && Date.now() - lastFrameAt > FROZEN_AFTER) unfreeze()
    else if (el.paused || !advanced) frozenTries = Math.min(frozenTries, 0)
    if (el.paused || el.ended || advanced) {
      stuckFor = Math.min(stuckFor, 0)
      if (advanced && netNote && !recovering) netNote = ''
      return
    }
    stuckFor += Math.min(gap, 8000)
    if (gap > 8000) stuckFor = Math.max(stuckFor, STUCK_AFTER)
    if (!navigator.onLine) netNote = 'Offline: will carry on when the connection is back'
    if (stuckFor >= STUCK_AFTER && !recovering && !gaveUp && navigator.onLine) recover()
  }

  // Time (and sound) move but no new picture has been shown: the video decoder or the picture surface died, typically
  // after a long pause or a fullscreen switch. First nudge the decoder with a tiny seek; if that does not help, rebuild
  // the stream from here (what closing the tab used to do).
  async function unfreeze() {
    lastFrameAt = Date.now()                       // give each step a few seconds to work
    frozenTries++
    console.warn('picture frozen while playing; attempt', frozenTries)
    trackEvent('player_frozen', { video_id: video.id, payload: { attempt: frozenTries, fullscreen: !!document.fullscreenElement } })
    if (frozenTries === 1) { el.currentTime = el.currentTime; return }
    await rebuild('Restarting the picture…')
  }

  // Throw away the playing pipeline and start again from the same spot (new connections, new stream links, new decoder).
  async function rebuild(message) {
    if (refreshing || recovering || destroyed || mode !== 'dash' || !shakaPlayer) return
    refreshing = true
    const at = el.currentTime, wasPlaying = !el.paused
    say(message)
    try {
      await loadManifest(at)
      if (chosenQuality !== 'auto') setQuality(chosenQuality)
      syncQualities()
      netNote = ''
      if (wasPlaying) el.play().catch(() => {})
    } catch (e) {
      console.warn('rebuild failed:', e?.code ?? e)
      stuckFor = STUCK_AFTER - 1000                  // hand over to the ordinary recovery, which keeps trying
    } finally {
      refreshing = false
      lastFrameAt = Date.now()
    }
  }

  // Pressing play on a video that sat paused for a long time: rebuild first, so it does not start on a dead pipeline.
  function onPause() { if (!el.ended) pausedAt = Date.now() }
  async function onPlay() {
    const idle = pausedAt ? Date.now() - pausedAt : 0
    pausedAt = 0
    if (idle < STALE_PAUSE_MS || refreshing) return
    trackEvent('player_refresh', { video_id: video.id, payload: { paused_s: Math.round(idle / 1000) } })
    el.pause()
    pausedAt = 0
    await rebuild('Refreshing after a long pause…')
    el.play().catch(() => {})
  }

  async function recover() {
    if (recovering || destroyed || !el) return
    recovering = true
    netNote = 'Connection problem: reconnecting…'
    const wantPlay = !el.paused
    const at = el.currentTime
    recoverTries++
    try {
      if (mode === 'dash' && shakaPlayer) {
        // false = Shaka is not in a failed state, merely waiting on a slow network: give it a few seconds first
        const resumed = shakaPlayer.retryStreaming(0.1)
        if (!(await progressed(resumed ? 5000 : 3000))) {
          await shakaPlayer.load(manifestUrl(audioLang, codecUsed), at)       // start over from here: new connections
          if (chosenQuality !== 'auto') setQuality(chosenQuality)
          syncQualities()
        }
      } else if (mode === 'muxed') {
        const src = el.getAttribute('src')
        el.src = src
        el.load()
        await new Promise((r) => { el.addEventListener('loadedmetadata', r, { once: true }); setTimeout(r, 8000) })
        el.currentTime = at
      }
      if (wantPlay) el.play().catch(() => {})
      if (await progressed(6000)) { netNote = ''; recoverTries = 0; stuckFor = 0 }
    } catch (e) {
      console.warn('recovery attempt failed:', e?.code ?? e)
    } finally {
      recovering = false
    }
    if (netNote && !destroyed) {
      if (recoverTries >= GIVE_UP_AFTER) { gaveUp = true; return }
      stuckFor = STUCK_AFTER - Math.min(15_000, 1500 * 2 ** Math.min(recoverTries, 3))   // wait longer each time, then go again
    }
  }
  function tryAgain() { gaveUp = false; recoverTries = 0; stuckFor = STUCK_AFTER; netNote = 'Connection problem: reconnecting…' }
  // The network is back: do not wait for the next tick.
  function backOnline() { if (netNote) { stuckFor = STUCK_AFTER; recoverTries = Math.min(recoverTries, 3) } }

  let botBlock = $state(false)
  function botBlocked() {
    mode = 'error'
    botBlock = true
    error = "YouTube is asking this server to prove it is not a bot, so this video can't be fetched right now. newtube has paused its background fetching to help. Try again in a minute or two."
  }

  // No DASH (404 from manifest, unsupported browser...): play the best muxed stream directly.
  async function fallback(cause) {
    console.warn('DASH unavailable, using muxed fallback:', cause?.message ?? cause)
    const muxed = video.formats.filter((f) => f.muxed).sort((a, b) => (b.height ?? 0) - (a.height ?? 0))
    if (!muxed.length) { mode = 'error'; error = 'No playable streams for this video.'; return }
    try { await shakaPlayer?.destroy() } catch { /* ignore */ }
    shakaPlayer = null
    qualities = muxed.map((f) => ({ h: f.height, fps: f.fps ?? 0 }))
    activeHeight = muxed[0].height ?? 0
    el.src = `/api/stream/${video.id}/${muxed[0].format_id}`
    if (startAt > 5) el.currentTime = startAt
    mode = 'muxed'
    el.play().catch(() => {})
  }

  function syncQualities() {
    if (!shakaPlayer) return
    const tracks = shakaPlayer.getVariantTracks()
    const fps = new Map()
    for (const x of tracks) if (x.height) fps.set(x.height, Math.max(fps.get(x.height) ?? 0, x.frameRate ?? 0))
    qualities = [...fps].map(([h, f]) => ({ h, fps: f })).sort((a, b) => b.h - a.h)
    activeHeight = tracks.find((x) => x.active)?.height ?? 0
  }

  function setQuality(q) {
    menu = null
    if (!shakaPlayer) return
    chosenQuality = q
    if (q === 'auto') {
      shakaPlayer.configure({ abr: { enabled: true } })
    } else {
      shakaPlayer.configure({ abr: { enabled: false } })
      const tr = shakaPlayer.getVariantTracks().filter((x) => x.height === q)
        .sort((a, b) => b.bandwidth - a.bandwidth)[0]
      if (tr) shakaPlayer.selectVariantTrack(tr, true)
    }
    trackEvent('quality_change', { video_id: video.id, payload: { quality: q } })
    say(q === 'auto' ? 'Quality: auto' : `Quality: ${qname(q, qualities.find((x) => x.h === q)?.fps)}`)
  }

  // ---- actions (also used by the keyboard handler) --------------------------------------------
  const toggle = () => (el.paused ? el.play() : el.pause())
  function seekBy(d) { seekTo(Math.max(0, Math.min(total, el.currentTime + d))); say(`${d > 0 ? '+' : ''}${d}s`) }
  function seekTo(s) { el.currentTime = s }
  function setVolume(v) {
    volume = Math.max(0, Math.min(1, v)); el.volume = volume; muted = false; el.muted = false
    try { localStorage.setItem('volume', String(volume)) } catch { /* ignore */ }
    say(`Volume ${Math.round(volume * 100)}%`)
  }
  function toggleMute() { muted = !muted; el.muted = muted; say(muted ? 'Muted' : 'Unmuted') }
  function setRate(r) {
    rate = r; el.playbackRate = r; menu = null; say(`Speed ${r}×`)
    tracker?.event('speed_change', { rate: r, position: Math.round(el.currentTime) })
  }
  function stepRate(dir) {
    const i = SPEEDS.indexOf(rate)
    setRate(SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, (i < 0 ? 3 : i) + dir))])
  }
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen()
    else box.requestFullscreen?.()
    trackEvent('fullscreen', { video_id: video.id })
  }
  function jumpChapter(dir) {
    if (!chapters.length) return
    const i = chapters.findLastIndex((c) => c.start <= el.currentTime - (dir < 0 ? 2 : 0))
    const target = chapters[Math.max(0, Math.min(chapters.length - 1, i + dir))]
    seekTo(target.start); say(target.title)
  }

  function setCaptions(lang) {
    menu = null
    for (const tt of el.textTracks) tt.mode = 'disabled'
    capLang = lang
    if (!lang) { say('Captions off'); return }
    let node = [...el.querySelectorAll('track')].find((x) => x.srclang === lang)
    if (!node) { // created on demand so no caption request is made unless you ask for one
      node = document.createElement('track')
      node.kind = 'subtitles'; node.srclang = lang
      node.label = video.captions.find((c) => c.lang === lang)?.name ?? lang
      node.src = `/api/captions/${video.id}/${lang}.vtt`
      el.appendChild(node)
    }
    node.track.mode = 'showing'
    say(`Captions: ${node.label}`)
  }
  function cycleCaptions() {
    const langs = video.captions.map((c) => c.lang)
    if (!langs.length) { say('No captions'); return }
    const i = capLang ? langs.indexOf(capLang) + 1 : 0
    setCaptions(i >= langs.length ? null : langs[i])
  }

  // ---- SponsorBlock: skipping ---------------------------------------------------------------
  function sbEvent(type, seg, extra = {}) {
    trackEvent(type, { video_id: video.id, payload: {
      uuid: seg.uuid, category: seg.category, start: seg.start, end: seg.end, ...extra } })
  }

  function skipSegment(seg, mode) {
    handled.add(seg.uuid)
    lastSkip = { seg }
    el.currentTime = total ? Math.min(seg.end, total - 0.01) : seg.end
    sbButton = null
    sbEvent(mode === 'auto' ? 'sb_skip' : 'sb_manual_skip', seg, { mode })
    if (mode === 'auto' && sb.settings.show_toast) {
      sbToast = { seg }
      clearTimeout(sbToastTimer)
      sbToastTimer = setTimeout(() => (sbToast = null), 6000)
    }
  }

  function undoSkip() {
    if (!lastSkip) return
    const { seg } = lastSkip
    lastSkip = null; sbToast = null
    ignored.add(seg.uuid); handled.delete(seg.uuid)
    el.currentTime = seg.start
    sbEvent('sb_undo', seg)
  }

  function sbTick() {
    if (!el || el.seeking || !sbSegments.length) return
    const now = el.currentTime
    for (const u of [...handled]) {   // rewound past a skipped segment: arm it again
      const s = sbSegments.find((x) => x.uuid === u)
      if (!s || now < s.start - 0.5) handled.delete(u)
    }
    if (el.paused) return
    const poi = sbPoi
    if (poi && !poiDone && sbActions[poi.category] === 'auto' && startAt < 5 && now < 3) {
      poiDone = true            // "auto" for highlights: jump to it once, at the start
      el.currentTime = poi.start
      return
    }
    const inside = sbSegments.find((s) => s.action !== 'poi' && now >= s.start && now < s.end - 0.05
      && !ignored.has(s.uuid) && !handled.has(s.uuid))
    if (!inside) {
      sbButton = null
      if (mutedBySb) { mutedBySb = false; el.muted = prevMuted }
      return
    }
    const mode = sbActions[inside.category]
    if (inside.action === 'mute') {
      if (mode === 'auto' && !mutedBySb) { prevMuted = el.muted; mutedBySb = true; el.muted = true }
    } else if (mode === 'auto') {
      skipSegment(inside, 'auto')
    } else if (mode === 'button') {
      sbButton = inside
    }
  }

  // ---- SponsorBlock: contributing (only when enabled in Settings) -------------------------------
  function canContribute() {
    if (sb?.settings?.contribute) return true
    say('Contributing is off. Turn it on in Settings → SponsorBlock.')
    return false
  }
  function markStart() {
    if (!canContribute()) return
    sbMarkStart = el.currentTime
    say(`Segment starts at ${fmt(sbMarkStart)}. Press ] where it ends.`)
  }
  function markEnd() {
    if (!canContribute()) return
    if (sbMarkStart === null) { say('Press [ at the start of the segment first.'); return }
    const end = el.currentTime
    if (end - sbMarkStart < 0.5) { say('The end must come after the start.'); return }
    sbDraft = { start: sbMarkStart, end, category: 'sponsor', busy: false, error: '' }
    el.pause()
  }
  function previewDraft() {
    el.currentTime = Math.max(0, sbDraft.start - 1.5)
    el.play().catch(() => {})
  }
  async function submitDraft() {
    const d = sbDraft
    d.busy = true; d.error = ''
    try {
      await http.post(`/api/sponsorblock/${video.id}/submit`, {
        start: d.start, end: d.end, category: d.category, action: d.category === 'poi_highlight' ? 'poi' : 'skip' })
      sbDraft = null; sbMarkStart = null
      say('Submitted to SponsorBlock. Thank you!')
    } catch (e) {
      d.error = e.message
    } finally {
      d.busy = false
    }
  }
  function voteOn(seg, type) {
    sbToast = null
    http.post('/api/sponsorblock/vote', { uuid: seg.uuid, type, video_id: video.id })
      .then(() => say(type === 'up' ? 'Upvoted. Thanks!' : 'Downvoted. Thanks!'))
      .catch((e) => say(e.message))
  }

  function skipNow() {              // Enter: take whatever skip button is on screen
    if (sbButton) skipSegment(sbButton, 'button')
    else if (sbPoiButton) { el.currentTime = sbPoiButton.start; sbEvent('sb_manual_skip', sbPoiButton, { mode: 'button' }) }
  }

  api = { seekTo, toggle, get time() { return el?.currentTime ?? 0 }, el: () => el }

  function onKey(e) {
    if (!keys) return                      // the mini-player must not answer the keyboard while you use another page
    if (e.target.closest?.('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return
    const k = e.key
    const act = {
      ' ': toggle, k: toggle, j: () => seekBy(-10), l: () => seekBy(10),
      ArrowLeft: () => seekBy(-5), ArrowRight: () => seekBy(5),
      ArrowUp: () => setVolume(volume + 0.05), ArrowDown: () => setVolume(volume - 0.05),
      m: toggleMute, f: toggleFullscreen, t: () => ontheater?.(), c: cycleCaptions, '<': () => stepRate(-1), '>': () => stepRate(1),
      '{': () => jumpChapter(-1), '}': () => jumpChapter(1),   // Shift+[ / Shift+]: chapters
      u: undoSkip, '[': markStart, ']': markEnd,
      Enter: () => (sbButton || sbPoiButton) && !e.target.closest('button') && skipNow(),
      Home: () => seekTo(0), End: () => seekTo(total - 1), '?': () => (help = !help),
      Escape: () => { help = false; menu = null; sbDraft = null },
    }[k]
    if (act) { e.preventDefault(); act() }
    else if (/^[0-9]$/.test(k)) { e.preventDefault(); seekTo((total * Number(k)) / 10) }
  }

  // ---- video element events -------------------------------------------------------------------
  function onTime() {
    if (!scrubbing) t = el.currentTime
    const b = el.buffered
    buffered = 0
    for (let i = 0; i < b.length; i++) if (b.start(i) <= el.currentTime + 0.5 && b.end(i) >= el.currentTime) buffered = b.end(i)
  }
  function wake() {
    showControls = true
    clearTimeout(hideTimer)
    hideTimer = setTimeout(() => { if (!el.paused && !menu) showControls = false }, 2800)
  }

  // ---- seek bar --------------------------------------------------------------------------------
  let bar
  const fracAt = (x) => { const r = bar.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)) }
  function barDown(e) {
    scrubbing = true; bar.setPointerCapture(e.pointerId)
    t = fracAt(e.clientX) * total
  }
  function barMove(e) {
    const f = fracAt(e.clientX)
    hover = { frac: f, time: f * total }
    if (scrubbing) t = f * total
  }
  function barUp(e) {
    if (!scrubbing) return
    scrubbing = false
    seekTo(fracAt(e.clientX) * total)
  }
  const chapterAt = (s) => chapters.findLast?.((c) => c.start <= s)?.title ?? ''
</script>

<svelte:window onkeydown={onKey} ononline={backOnline} onfullscreenchange={() => (fullscreen = !!document.fullscreenElement)} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="player" class:compact class:idle={!showControls && !paused} bind:this={box} onmousemove={wake} onmouseleave={() => !paused && (showControls = false)}>
  <!-- svelte-ignore a11y_media_has_caption -->
  <video bind:this={el} playsinline
    bind:paused bind:volume bind:muted
    ontimeupdate={onTime} onprogress={onTime}
    ondurationchange={() => Number.isFinite(el.duration) && (dur = el.duration)}
    onpause={onPause} onplay={onPlay}
    onplaying={wake}                                  /* start the hide timer: a touch screen has no mouse movement to do it */
    onended={() => onended?.()}
    onpointerdown={fingerDown} onclick={videoClick} ondblclick={() => lastPointer !== 'touch' && toggleFullscreen()}
    onerror={() => mode === 'muxed' && (stuckFor = Math.max(stuckFor, STUCK_AFTER - 1000))}></video>

  {#if mode === 'loading'}<div class="center muted">Loading…</div>{/if}
  {#if mode === 'error' || error}
    <div class="center error"><div class="errbox">{error}{#if botBlock} <button class="errbtn" onclick={() => location.reload()}>Try again</button>{/if}</div></div>
  {/if}
  {#if paused && mode !== 'loading' && mode !== 'error'}
    <button class="bigplay" onclick={toggle} aria-label="Play">▶</button>
  {/if}
  {#if toast}<div class="toast">{toast}</div>{/if}
  {#if gaveUp}
    <div class="netnote" role="alert">Can't reach the video. <button onclick={tryAgain}>Try again</button></div>
  {:else if netNote && mode !== 'loading'}
    <div class="netnote" role="status">{netNote}</div>
  {:else if netNote}
    <div class="center muted">{netNote}</div>
  {/if}

  {#if sbButton}
    <button class="sbskip" onclick={() => skipSegment(sbButton, 'button')}>
      Skip {sbLabels[sbButton.category] ?? sbButton.category} ⏭ <kbd>Enter</kbd>
    </button>
  {:else if sbPoiButton}
    <button class="sbskip" onclick={skipNow}>Jump to highlight ({fmt(sbPoiButton.start)}) <kbd>Enter</kbd></button>
  {/if}
  {#if sbToast}
    <div class="sbtoast" role="status">
      Skipped {sbLabels[sbToast.seg.category] ?? sbToast.seg.category}
      <button onclick={undoSkip}>Undo <kbd>u</kbd></button>
      {#if sb.settings.contribute}
        <button class="vote" title="Good skip: upvote (tells SponsorBlock which video this is)" onclick={() => voteOn(sbToast.seg, 'up')}>👍</button>
        <button class="vote" title="Wrong skip: downvote (tells SponsorBlock which video this is)" onclick={() => voteOn(sbToast.seg, 'down')}>👎</button>
      {/if}
    </div>
  {/if}

  {#if sbDraft}
    <div class="sbdialog" role="dialog" aria-label="Submit a SponsorBlock segment">
      <h3>Submit to SponsorBlock</h3>
      <p>
        {fmt(sbDraft.start)}{sbDraft.category === 'poi_highlight' ? '' : ` – ${fmt(sbDraft.end)} (${(sbDraft.end - sbDraft.start).toFixed(1)} s)`}
      </p>
      <label>Category
        <select bind:value={sbDraft.category}>
          {#each sb.settings.category_order as c}<option value={c}>{sbLabels[c] ?? c}</option>{/each}
        </select>
      </label>
      <p class="note">This tells SponsorBlock's server which video this is (looking segments up does not). It uses a random anonymous ID.</p>
      {#if sbDraft.error}<p class="err">{sbDraft.error}</p>{/if}
      <div class="btns">
        <button onclick={previewDraft}>Preview</button>
        <button class="go" disabled={sbDraft.busy} onclick={submitDraft}>{sbDraft.busy ? 'Submitting…' : 'Submit'}</button>
        <button onclick={() => (sbDraft = null)}>Cancel <kbd>Esc</kbd></button>
      </div>
    </div>
  {/if}

  {#if help}
    <div class="help">
      <b>Keyboard</b>
      <dl>
        <dt>Space / k</dt><dd>play / pause</dd><dt>← →</dt><dd>seek 5 s</dd><dt>j / l</dt><dd>seek 10 s</dd>
        <dt>↑ ↓</dt><dd>volume</dd><dt>m</dt><dd>mute</dd><dt>f</dt><dd>fullscreen</dd>
        <dt>c</dt><dd>captions</dd><dt>&lt; &gt;</dt><dd>speed</dd><dt>{'{'} {'}'}</dt><dd>previous / next chapter</dd><dt>Enter</dt><dd>skip (when a skip button shows)</dd><dt>u</dt><dd>undo last auto-skip</dd><dt>[ ]</dt><dd>mark a segment's start / end (SponsorBlock, if enabled)</dd>
        <dt>t</dt><dd>theater mode</dd><dt>0–9</dt><dd>jump to 0–90%</dd><dt>?</dt><dd>this help</dd>
      </dl>
    </div>
  {/if}

  <div class="controls">
    <div class="bar" bind:this={bar} role="slider" tabindex="-1" aria-label="Seek"
      aria-valuemin="0" aria-valuemax={Math.round(total)} aria-valuenow={Math.round(t)}
      onpointerdown={barDown} onpointermove={barMove} onpointerup={barUp} onpointerleave={() => (hover = null)}>
      {#each segments as s}
        {@const w = total ? ((s.end - s.start) / total) * 100 : 100}
        <div class="seg" style:width={w + '%'}>
          <div class="track">
            <div class="buf" style:width={Math.max(0, Math.min(100, ((buffered - s.start) / (s.end - s.start || 1)) * 100)) + '%'}></div>
            <div class="played" style:width={Math.max(0, Math.min(100, ((t - s.start) / (s.end - s.start || 1)) * 100)) + '%'}></div>
          </div>
        </div>
      {/each}
      {#if sbMarkStart !== null && total}
        <div class="pending" style:left={(sbMarkStart / total) * 100 + '%'} title="Segment start"></div>
      {/if}
      <div class="marks" aria-hidden="true">
        {#each sbSegments as s (s.uuid)}
          {#if total}
            <div class="sbmark" class:poi={s.action === 'poi'} style:left={(s.start / total) * 100 + '%'}
              style:width={s.action === 'poi' ? '3px' : ((s.end - s.start) / total) * 100 + '%'}
              style:background={COLORS[s.category]} title={sbLabels[s.category]}></div>
          {/if}
        {/each}
      </div>
      {#if hover}
        <div class="tip" style:left={hover.frac * 100 + '%'}>
          {#if chapterAt(hover.time)}<div>{chapterAt(hover.time)}</div>{/if}{fmt(hover.time)}
        </div>
      {/if}
    </div>

    <div class="row">
      <button title="Play/pause (k)" onclick={toggle}>{paused ? '▶' : '❚❚'}</button>
      <button title="Mute (m)" onclick={toggleMute}>{muted || volume === 0 ? '🔇' : '🔊'}</button>
      <input class="vol" type="range" min="0" max="1" step="0.05" value={muted ? 0 : volume}
        oninput={(e) => setVolume(Number(e.target.value))} aria-label="Volume" />
      <span class="time">{fmt(t)} / {fmt(total)}</span>
      {#if currentChapter}<span class="chap">• {currentChapter.title}</span>{/if}
      <span class="spacer"></span>

      <div class="menuwrap">
        {#if video.captions.length}
          <button class:on={capLang} title="Captions (c)" onclick={() => (menu = menu === 'captions' ? null : 'captions')}>CC</button>
          {#if menu === 'captions'}
            <ul class="menu">
              <li><button class:sel={!capLang} onclick={() => setCaptions(null)}>Off</button></li>
              {#each video.captions as c}<li><button class:sel={capLang === c.lang} onclick={() => setCaptions(c.lang)}>{c.name}</button></li>{/each}
            </ul>
          {/if}
        {/if}
      </div>
      {#if sb?.data?.segments?.length}
        <button class:on={sbOn} title="SponsorBlock for this video: {sbOn ? 'on' : 'off'}" onclick={() => { sbOn = !sbOn; sbButton = null }}>SB</button>
      {/if}
      {#if audioTracks.length > 1 && mode === 'dash'}
        <div class="menuwrap">
          <button title="Audio language" onclick={() => (menu = menu === 'audio' ? null : 'audio')}>
            {audioLang === 'original' ? 'Audio' : audioLang}
          </button>
          {#if menu === 'audio'}
            <ul class="menu">
              {#each audioTracks as a}
                {@const key = a.original ? 'original' : a.lang}
                <li><button class:sel={audioLang === key} onclick={() => setAudio(key)}>
                  {a.name}{a.original ? ' (original)' : ''}
                </button></li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}
      <div class="menuwrap">
        <button title="Speed (&lt; &gt;)" onclick={() => (menu = menu === 'speed' ? null : 'speed')}>{rate}×</button>
        {#if menu === 'speed'}
          <ul class="menu">{#each SPEEDS as s}<li><button class:sel={rate === s} onclick={() => setRate(s)}>{s}×</button></li>{/each}</ul>
        {/if}
      </div>
      <div class="menuwrap">
        <button title="Quality" onclick={() => (menu = menu === 'quality' ? null : 'quality')}>
          {chosenQuality === 'auto' ? `Auto${activeHeight ? ` ${activeHeight}p` : ''}` : qname(chosenQuality, qualities.find((x) => x.h === chosenQuality)?.fps)}
        </button>
        {#if menu === 'quality'}
          <ul class="menu">
            {#if mode === 'dash'}<li><button class:sel={chosenQuality === 'auto'} onclick={() => setQuality('auto')}>Auto</button></li>{/if}
            {#each qualities as q (q.h)}<li><button class:sel={chosenQuality === q.h || (mode === 'muxed' && activeHeight === q.h)} onclick={() => setQuality(q.h)}>{qname(q.h, q.fps)}</button></li>{/each}
          </ul>
        {/if}
      </div>
      <button title="Keyboard shortcuts (?)" onclick={() => (help = !help)}>?</button>
      {#if ontheater}
        <button class="theater" class:on={theater} aria-pressed={theater} title={theater ? 'Default view (t)' : 'Theater mode (t)'}
                onclick={ontheater}>▭</button>
      {/if}
      <button title="Fullscreen (f)" onclick={toggleFullscreen}>{fullscreen ? '🗗' : '⛶'}</button>
    </div>
  </div>
</div>

<style>
  .player { position: relative; background: #000; aspect-ratio: 16 / 9; border-radius: var(--radius);
    width: min(100%, calc(80vh * 16 / 9)); margin: 0 auto;
    overflow: hidden; user-select: none; }
  .player:fullscreen { border-radius: 0; aspect-ratio: auto; width: 100%; }
  .player.idle { cursor: none; }
  @media (max-width: 699px) { .player { border-radius: 0; width: 100%; } }
  video { width: 100%; height: 100%; display: block; background: #000; }
  video::cue { font-size: 1.4em; background: rgba(0, 0, 0, 0.7); }
  .center { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
  .errbox { max-width: 34em; padding: 0 16px; text-align: center; pointer-events: auto; }
  .errbtn { margin-left: 8px; background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 6px; padding: 4px 12px; font-weight: 600; }
  .bigplay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 76px; height: 76px;
    border-radius: 50%; border: 0; background: rgba(0, 0, 0, 0.6); color: #fff; font-size: 30px; padding-left: 6px; }
  .bigplay:hover { background: var(--accent); color: var(--accent-fg); }
  .toast { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); background: rgba(0, 0, 0, 0.75);
    color: #fff; padding: 6px 14px; border-radius: 8px; pointer-events: none; }
  .netnote { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); background: rgba(0, 0, 0, 0.8); color: #fff;
    padding: 6px 14px; border-radius: 999px; font-size: 13px; z-index: 3; white-space: nowrap; max-width: 92%; overflow: hidden; text-overflow: ellipsis; }
  .netnote button { margin-left: 8px; background: var(--accent); color: var(--accent-fg); border: 0; border-radius: 6px; padding: 2px 10px; font-weight: 600; }
  .help { position: absolute; top: 14px; right: 14px; background: rgba(0, 0, 0, 0.85); color: #fff; padding: 12px 16px;
    border-radius: 10px; font-size: 13px; }
  .help dl { display: grid; grid-template-columns: auto auto; gap: 3px 16px; margin: 8px 0 0; }
  .help dt { font-weight: 600; } .help dd { margin: 0; color: #bbb; }

  .controls { position: absolute; left: 0; right: 0; bottom: 0; padding: 28px 12px 8px; color: #fff;
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.8)); transition: opacity 0.2s; }
  .player.idle .controls { opacity: 0; pointer-events: none; }
  /* The controls' background is only a gradient: taps on it must reach the picture (to pause), so only the seek bar
     and the buttons themselves catch the pointer. (Matters on a phone, where the bar covers half the video.) */
  .controls { pointer-events: none; }
  .controls > * { pointer-events: auto; }
  .player.idle .controls > * { pointer-events: none; }
  .bar { position: relative; display: flex; gap: 2px; height: 14px; align-items: center; cursor: pointer; touch-action: none; }
  .seg { height: 100%; display: flex; align-items: center; }
  .track { position: relative; width: 100%; height: 4px; background: rgba(255, 255, 255, 0.25); transition: height 0.1s; }
  .bar:hover .track { height: 7px; }
  .buf, .played { position: absolute; left: 0; top: 0; bottom: 0; }
  .buf { background: rgba(255, 255, 255, 0.35); }
  .played { background: var(--accent); }
  .pending { position: absolute; top: 0; bottom: 0; width: 3px; background: #fff; pointer-events: none; z-index: 1; }
  .sbtoast .vote { font-size: 15px; }
  .sbdialog { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); z-index: 3; width: min(420px, 90%);
    background: rgba(18, 18, 22, 0.97); color: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.2); }
  .sbdialog h3 { margin: 0 0 6px; font-size: 16px; } .sbdialog p { margin: 6px 0; }
  .sbdialog label { display: flex; gap: 10px; align-items: center; }
  .sbdialog select { flex: 1; background: #222; color: #fff; border: 1px solid #555; border-radius: 6px; padding: 5px 8px; }
  .sbdialog .note { font-size: 12px; opacity: 0.75; } .sbdialog .err { color: #ff8080; }
  .sbdialog .btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; }
  .sbdialog .btns button { background: #333; color: #fff; border: 0; border-radius: 6px; padding: 6px 12px; }
  .sbdialog .btns .go { background: var(--accent); color: var(--accent-fg); font-weight: 700; }
  .marks { position: absolute; left: 0; right: 0; top: 50%; height: 4px; transform: translateY(-50%); pointer-events: none; }
  .bar:hover .marks { height: 7px; }
  .sbmark { position: absolute; top: 0; bottom: 0; opacity: 0.9; }
  .sbmark.poi { min-width: 3px; }
  .sbskip, .sbtoast { position: absolute; right: 14px; bottom: 74px; background: rgba(0, 0, 0, 0.85); color: #fff; border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 8px; padding: 8px 14px; z-index: 2; }
  .sbskip:hover { background: var(--accent); color: var(--accent-fg); }
  .sbtoast button { margin-left: 10px; background: none; border: 0; color: var(--accent); font-weight: 700; }
  kbd { font: inherit; font-size: 11px; opacity: 0.7; border: 1px solid currentColor; border-radius: 4px; padding: 0 4px; margin-left: 4px; }
  .tip { position: absolute; bottom: 22px; transform: translateX(-50%); background: rgba(0, 0, 0, 0.9); padding: 3px 8px;
    border-radius: 6px; font-size: 12px; text-align: center; pointer-events: none; white-space: nowrap; }
  /* the mini-player: only the essentials fit */
  .player.compact .vol, .player.compact .chap, .player.compact .theater, .player.compact .time { display: none; }
  .player.compact .bigplay { width: 52px; height: 52px; font-size: 20px; }
  .row { display: flex; align-items: center; gap: 4px; margin-top: 2px; }
  .row button { background: none; border: 0; color: #fff; padding: 6px 9px; border-radius: 6px; font-size: 14px; }
  .row button:hover { background: rgba(255, 255, 255, 0.18); }
  .row button.on { color: var(--accent); }
  .time { font-size: 13px; font-variant-numeric: tabular-nums; margin: 0 8px; }
  .chap { font-size: 13px; opacity: 0.85; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 30ch; }
  .spacer { flex: 1; }
  .vol { width: 70px; accent-color: var(--accent); }
  .menuwrap { position: relative; }
  .menu { position: absolute; right: 0; bottom: 40px; list-style: none; margin: 0; padding: 4px; min-width: 110px;
    background: rgba(20, 20, 24, 0.96); border-radius: 8px; max-height: 260px; overflow: auto; }
  .menu button { width: 100%; text-align: left; }
  .menu button.sel { color: var(--accent); font-weight: 700; }

  /* (last, so these beat the base rules above) */
  /* touch screens: bigger targets, no volume slider (the phone has buttons) */
  @media (pointer: coarse), (max-width: 699px) {      /* (a small window counts too: emulators and some browsers do not report 'coarse') */
    .row button { padding: 11px 12px; }
    .vol { display: none; }
    .bar { height: 26px; }
  }
</style>
