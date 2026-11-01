<script>
  import { onMount } from 'svelte'
  import { duration as fmt } from '../lib/format.js'
  import { WatchTracker, trackEvent } from '../lib/tracker.js'

  // video: metadata from /api/video/<id>.  startAt: resume position (s).  api: bindable handle.
  let { video, startAt = 0, source = 'direct', api = $bindable(), onended = null } = $props()

  let box, el
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
  let qualities = $state([]) // heights, high to low
  let chosenQuality = $state('auto')
  let activeHeight = $state(0)
  let capLang = $state(null)
  let audioLang = $state(initialAudio()) // 'original' or a language code like 'es-US'
  let hover = $state(null) // { frac, time }
  let scrubbing = $state(false)
  let toast = $state('')
  let help = $state(false)

  const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3]
  const audioTracks = $derived(video.audio_tracks ?? [])
  const chapters = $derived(video.chapters ?? [])
  const total = $derived(dur || video.duration || 0)
  const segments = $derived(
    chapters.length
      ? chapters.map((c, i) => ({ title: c.title, start: c.start, end: chapters[i + 1]?.start ?? total }))
      : [{ title: '', start: 0, end: total }],
  )
  const currentChapter = $derived(chapters.findLast?.((c) => c.start <= t) ?? null)

  let shakaPlayer = null
  let tracker = null
  let hideTimer = null
  let toastTimer = null

  // Videos can carry machine-dubbed audio in ~20 languages. Default is always the original.
  function initialAudio() {
    let v = 'original'
    try { v = localStorage.getItem('audioLang') || 'original' } catch { /* ignore */ }
    return v !== 'original' && video.audio_tracks?.some((a) => a.lang === v) ? v : 'original'
  }
  const manifestUrl = (lang) =>
    `/api/dash/${video.id}.mpd` + (lang && lang !== 'original' ? `?lang=${encodeURIComponent(lang)}` : '')

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
    ;(async () => {
      try {
        // Lazy chunk: only the watch page pays for the player library (served from this server).
        const shaka = (await import('shaka-player/dist/shaka-player.dash.js')).default
        shaka.polyfill.installAll()
        if (!shaka.Player.isBrowserSupported()) throw new Error('MSE not supported')
        shakaPlayer = new shaka.Player()
        await shakaPlayer.attach(el)
        shakaPlayer.configure({
          streaming: { bufferingGoal: 90, rebufferingGoal: 2, bufferBehind: 30 },
          abr: { enabled: true },
        })
        shakaPlayer.addEventListener('variantchanged', syncQualities)
        shakaPlayer.addEventListener('adaptation', syncQualities)
        await shakaPlayer.load(manifestUrl(audioLang), startAt > 5 ? startAt : null)
        if (gone) return
        mode = 'dash'
        syncQualities()
        el.play().catch(() => {})
      } catch (e) {
        if (!gone) fallback(e)
      }
    })()
    return () => {
      gone = true
      tracker?.stop()
      shakaPlayer?.destroy()
      clearTimeout(hideTimer); clearTimeout(toastTimer)
    }
  })

  // No DASH (404 from manifest, unsupported browser...): play the best muxed stream directly.
  async function fallback(cause) {
    console.warn('DASH unavailable, using muxed fallback:', cause?.message ?? cause)
    const muxed = video.formats.filter((f) => f.muxed).sort((a, b) => (b.height ?? 0) - (a.height ?? 0))
    if (!muxed.length) { mode = 'error'; error = 'No playable streams for this video.'; return }
    try { await shakaPlayer?.destroy() } catch { /* ignore */ }
    shakaPlayer = null
    qualities = muxed.map((f) => f.height)
    activeHeight = muxed[0].height ?? 0
    el.src = `/api/stream/${video.id}/${muxed[0].format_id}`
    if (startAt > 5) el.currentTime = startAt
    mode = 'muxed'
    el.play().catch(() => {})
  }

  function syncQualities() {
    if (!shakaPlayer) return
    const tracks = shakaPlayer.getVariantTracks()
    qualities = [...new Set(tracks.map((x) => x.height).filter(Boolean))].sort((a, b) => b - a)
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
    say(q === 'auto' ? 'Quality: auto' : `Quality: ${q}p`)
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

  api = { seekTo, toggle, get time() { return el?.currentTime ?? 0 }, el: () => el }

  function onKey(e) {
    if (e.target.closest?.('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return
    const k = e.key
    const act = {
      ' ': toggle, k: toggle, j: () => seekBy(-10), l: () => seekBy(10),
      ArrowLeft: () => seekBy(-5), ArrowRight: () => seekBy(5),
      ArrowUp: () => setVolume(volume + 0.05), ArrowDown: () => setVolume(volume - 0.05),
      m: toggleMute, f: toggleFullscreen, c: cycleCaptions, '<': () => stepRate(-1), '>': () => stepRate(1),
      '[': () => jumpChapter(-1), ']': () => jumpChapter(1),
      Home: () => seekTo(0), End: () => seekTo(total - 1), '?': () => (help = !help),
      Escape: () => { help = false; menu = null },
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

<svelte:window onkeydown={onKey} onfullscreenchange={() => (fullscreen = !!document.fullscreenElement)} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="player" class:idle={!showControls && !paused} bind:this={box} onmousemove={wake} onmouseleave={() => !paused && (showControls = false)}>
  <!-- svelte-ignore a11y_media_has_caption -->
  <video bind:this={el} playsinline
    bind:paused bind:volume bind:muted
    ontimeupdate={onTime} onprogress={onTime}
    ondurationchange={() => Number.isFinite(el.duration) && (dur = el.duration)}
    onended={() => onended?.()}
    onclick={toggle} ondblclick={toggleFullscreen}
    onerror={() => mode === 'muxed' && (error = 'Playback error')}></video>

  {#if mode === 'loading'}<div class="center muted">Loading…</div>{/if}
  {#if mode === 'error' || error}<div class="center error">{error}</div>{/if}
  {#if paused && mode !== 'loading' && mode !== 'error'}
    <button class="bigplay" onclick={toggle} aria-label="Play">▶</button>
  {/if}
  {#if toast}<div class="toast">{toast}</div>{/if}

  {#if help}
    <div class="help">
      <b>Keyboard</b>
      <dl>
        <dt>Space / k</dt><dd>play / pause</dd><dt>← →</dt><dd>seek 5 s</dd><dt>j / l</dt><dd>seek 10 s</dd>
        <dt>↑ ↓</dt><dd>volume</dd><dt>m</dt><dd>mute</dd><dt>f</dt><dd>fullscreen</dd>
        <dt>c</dt><dd>captions</dd><dt>&lt; &gt;</dt><dd>speed</dd><dt>[ ]</dt><dd>previous / next chapter</dd>
        <dt>0–9</dt><dd>jump to 0–90%</dd><dt>?</dt><dd>this help</dd>
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
          {chosenQuality === 'auto' ? `Auto${activeHeight ? ` ${activeHeight}p` : ''}` : `${chosenQuality}p`}
        </button>
        {#if menu === 'quality'}
          <ul class="menu">
            {#if mode === 'dash'}<li><button class:sel={chosenQuality === 'auto'} onclick={() => setQuality('auto')}>Auto</button></li>{/if}
            {#each qualities as q}<li><button class:sel={chosenQuality === q || (mode === 'muxed' && activeHeight === q)} onclick={() => setQuality(q)}>{q}p</button></li>{/each}
          </ul>
        {/if}
      </div>
      <button title="Keyboard shortcuts (?)" onclick={() => (help = !help)}>?</button>
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
  video { width: 100%; height: 100%; display: block; background: #000; }
  video::cue { font-size: 1.4em; background: rgba(0, 0, 0, 0.7); }
  .center { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
  .bigplay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 76px; height: 76px;
    border-radius: 50%; border: 0; background: rgba(0, 0, 0, 0.6); color: #fff; font-size: 30px; padding-left: 6px; }
  .bigplay:hover { background: var(--accent); color: var(--accent-fg); }
  .toast { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); background: rgba(0, 0, 0, 0.75);
    color: #fff; padding: 6px 14px; border-radius: 8px; pointer-events: none; }
  .help { position: absolute; top: 14px; right: 14px; background: rgba(0, 0, 0, 0.85); color: #fff; padding: 12px 16px;
    border-radius: 10px; font-size: 13px; }
  .help dl { display: grid; grid-template-columns: auto auto; gap: 3px 16px; margin: 8px 0 0; }
  .help dt { font-weight: 600; } .help dd { margin: 0; color: #bbb; }

  .controls { position: absolute; left: 0; right: 0; bottom: 0; padding: 28px 12px 8px; color: #fff;
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.8)); transition: opacity 0.2s; }
  .player.idle .controls { opacity: 0; pointer-events: none; }
  .bar { position: relative; display: flex; gap: 2px; height: 14px; align-items: center; cursor: pointer; touch-action: none; }
  .seg { height: 100%; display: flex; align-items: center; }
  .track { position: relative; width: 100%; height: 4px; background: rgba(255, 255, 255, 0.25); transition: height 0.1s; }
  .bar:hover .track { height: 7px; }
  .buf, .played { position: absolute; left: 0; top: 0; bottom: 0; }
  .buf { background: rgba(255, 255, 255, 0.35); }
  .played { background: var(--accent); }
  .tip { position: absolute; bottom: 22px; transform: translateX(-50%); background: rgba(0, 0, 0, 0.9); padding: 3px 8px;
    border-radius: 6px; font-size: 12px; text-align: center; pointer-events: none; white-space: nowrap; }
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
</style>
