<script>
  import { onMount, onDestroy } from 'svelte'
  import { api } from '../lib/api.js'
  import { navigate } from '../lib/router.svelte.js'
  import { duration as fmt, ago, views } from '../lib/format.js'
  import Player from '../components/Player.svelte'
  import Comments from '../components/Comments.svelte'
  import SponsorBlockPanel from '../components/SponsorBlockPanel.svelte'
  import ListButtons from '../components/ListButtons.svelte'
  import SubscribeButton from '../components/SubscribeButton.svelte'
  import RelatedList from '../components/RelatedList.svelte'
  import QueuePanel from '../components/QueuePanel.svelte'
  import { prefs } from '../lib/prefs.svelte.js'
  import { loadQueue, dequeue } from '../lib/queue.svelte.js'
  import { trackEvent } from '../lib/tracker.js'

  // The video to play comes from the host (App), not from the address bar, so the same page can keep playing in a
  // small corner player (`mini`) while you browse elsewhere. list: a YouTube playlist; mylist: one of your own lists.
  let { id, source = 'direct', list = null, mylist = null, mini = false, onclose = null, onexpand = null, onnavigate = null } = $props()
  let pl = $state(null)
  let sbData = $state(null)       // SponsorBlock segments for this video
  let sbSettings = $state(null)   // your SponsorBlock settings

  // Theater mode: the player spans the whole width and Up next moves below it. Remembered between visits.
  let theater = $state(readTheater())
  function readTheater() { try { return localStorage.getItem('theater') === '1' } catch { return false } }
  function toggleTheater() {
    theater = !theater
    try { localStorage.setItem('theater', theater ? '1' : '0') } catch { /* private mode */ }
  }

  let video = $state(null)
  let error = $state('')
  let player = $state()
  let t = $state(0)
  let expanded = $state(false)

  onMount(() => {
    let timer
    api.del(`/api/queue/${id}`).then((r) => r.removed && loadQueue()).catch(() => {})       // what is playing is no longer "waiting" in the queue
    // Start the slow parts in parallel with the metadata request instead of after it:
    // the player library chunk, and the manifest (the server then extracts the video and probes the
    // stream layouts once; the player's own manifest request a moment later is a cheap cache hit).
    // SponsorBlock info and settings load in parallel with everything else; failures are harmless.
    api.get(`/api/sponsorblock/${id}`).then((d) => (sbData = d)).catch(() => {})
    api.get('/api/settings/sponsorblock').then((s) => (sbSettings = s)).catch(() => {})
    import('shaka-player/dist/shaka-player.dash.js').catch(() => {})
    fetch(`/api/dash/${id}.mpd`, { priority: 'low' }).catch(() => {})
    if (list) api.get(`/api/playlist/${list}`).then((p) => (pl = p)).catch(() => {})
    if (mylist) api.get(`/api/mylists/${mylist}?limit=200`).then((p) => (pl = { playlist: { title: p.list.name }, videos: p.videos.map((v) => ({ ...v, title: v.title || v.id })) })).catch(() => {})
    api.get(`/api/video/${id}`).then((v) => {
      video = v
      timer = setInterval(() => { t = player?.time ?? 0 }, 500) // for highlighting the current chapter
    }).catch((e) => (error = e.message))
    return () => { clearInterval(timer); document.title = 'newtube' }
  })
  $effect(() => { if (video && !mini) document.title = `${video.title} · newtube` })     // (not while it is only the mini-player)

  const current = $derived(video?.chapters?.findLastIndex((c) => c.start <= t) ?? -1)
  const at = $derived(pl ? pl.videos.findIndex((v) => v.id === id) : -1)
  const next = $derived(at >= 0 ? pl.videos[at + 1] : null)
  const watchHref = (vid) => (mylist ? `/watch?v=${vid}&src=playlist&mylist=${mylist}` : `/watch?v=${vid}&src=playlist&list=${list}`)
  // ---- what plays when this video ends: the queue, then the playlist, then the first unwatched video of Up next ----------
  let relatedFirst = $state(null)
  let target = $state(null)          // the video the countdown is about to start
  let count = $state(0)
  let ticker = null
  const related = (vids) => (relatedFirst = vids.find((v) => !v.seen && !v.completed) ?? null)

  async function pickNext() {
    try {
      const q = await api.get('/api/queue?limit=1')
      if (q.videos.length) { const v = q.videos[0]; return { id: v.id, title: v.title || v.id, href: `/watch?v=${v.id}&src=queue`, via: 'queue', fromQueue: true } }
    } catch { /* no queue: carry on */ }
    if (next) return { id: next.id, title: next.title, href: watchHref(next.id), via: 'playlist' }
    if (prefs.autoplay && relatedFirst) return { id: relatedFirst.id, title: relatedFirst.title, href: `/watch?v=${relatedFirst.id}&src=related`, via: 'related' }
    return null
  }
  async function handleEnded() {
    const t = await pickNext()
    if (!t) return
    target = t; count = 5
    clearInterval(ticker)
    ticker = setInterval(() => { count -= 1; if (count <= 0) go() }, 1000)
  }
  function cancelNext() {
    clearInterval(ticker); ticker = null
    trackEvent('autoplay_cancel', { video_id: id })
    target = null
  }
  async function go() {
    const t = target
    if (!t) return
    clearInterval(ticker); ticker = null; target = null
    trackEvent('autoplay_next', { video_id: id, payload: { to: t.id, via: t.via } })
    if (t.fromQueue) await dequeue(t.id)
    ;(onnavigate ?? navigate)(t.href)
  }
  onDestroy(() => clearInterval(ticker))
</script>

<div class="watch" class:theater class:mini>
  {#if error}
    <p class="error">{error}</p>
    {#if mini}<button class="btn" onclick={onclose}>Close</button>{:else}<p><button class="btn" onclick={() => location.reload()}>Try again</button> <a href="/" class="muted">Back home</a></p>{/if}
  {:else if !video}
    <p class="muted">Loading…</p>
  {:else}
    <div class="stage">
    <Player {video} {source} startAt={video.resume_position} bind:api={player} onended={handleEnded}
      sb={sbData && sbSettings ? { data: sbData, settings: sbSettings, channelId: video.channel_id } : null}
      {theater} ontheater={mini ? null : toggleTheater} compact={mini} keys={!mini} />
      {#if target}
        <div class="autonext" class:small={mini} role="alert">
          <div class="an-label">Up next in {count}</div>
          <div class="an-title">{target.title}</div>
          <div class="an-actions">
            <button class="btn primary small" onclick={go}>Play now</button>
            <button class="btn small" onclick={cancelNext}>Cancel</button>
          </div>
        </div>
      {/if}
      {#if mini}
        <div class="minibar">
          <span class="mt" title={video.title}>{video.title}</span>
          <button onclick={onexpand} aria-label="Open the video page" title="Open the video page">⤢</button>
          <button onclick={onclose} aria-label="Close the mini player" title="Close">✕</button>
        </div>
      {/if}
    </div>
    <div class="details">
    <h1>{video.title}</h1>
    <div class="byline">
      <a class="chan" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
      {#if video.channel_id}<SubscribeButton channelId={video.channel_id} subscribed={video.subscribed} />{/if}
      <span class="muted">{[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}</span>
      <span class="spacer"></span>
      <ListButtons videoId={video.id} liked={video.liked} watchLater={video.watch_later} playlists={video.playlists} />
    </div>

    <SponsorBlockPanel data={sbData} settings={sbSettings} channelId={video.channel_id}
      channelTitle={video.channel_title} onsettings={(s) => (sbSettings = s)} />

    {#if pl}
      <section class="plist">
        <h2>{pl.playlist.title} <span class="muted">· {at + 1 || '?'} / {pl.videos.length}</span></h2>
        <ol>
          {#each pl.videos as v, i (v.id)}
            <li class:current={v.id === id}>
              <a href={watchHref(v.id)}><span class="ts">{i + 1}</span><span>{v.title}</span>
                {#if v.duration}<span class="muted dur">{fmt(v.duration)}</span>{/if}</a>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    {#if video.chapters.length}
      <section>
        <h2>Chapters</h2>
        <ol class="chapters">
          {#each video.chapters as c, i}
            <li class:current={i === current}>
              <button onclick={() => player.seekTo(c.start)}>
                <span class="ts">{fmt(c.start)}</span><span>{c.title}</span>
              </button>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    <section class="desc" class:open={expanded}>
      <pre>{video.description}</pre>
      <button class="btn" onclick={() => (expanded = !expanded)}>{expanded ? 'Show less' : 'Show more'}</button>
    </section>

    </div>
    <aside class="related"><QueuePanel /><RelatedList videoId={video.id} onloaded={related} /></aside>
    <div class="discussion"><Comments videoId={video.id} {player} /></div>
  {/if}
</div>

<style>
  /* One column on a narrow window: video and details, then Up next, then comments. With room to spare,
     Up next sits in a column of its own beside the video and the comments. */
  .watch { max-width: 1280px; margin: 0 auto; padding: 20px 24px 80px; display: grid; gap: 0 24px;
    grid-template-columns: minmax(0, 1fr); grid-template-areas: "stage" "details" "related" "comments"; }
  .stage { grid-area: stage; min-width: 0; position: relative; }
  /* the mini-player: just the video, with a small title bar on hover */
  @media (max-width: 699px) {
    .watch { padding: calc(52px + env(safe-area-inset-top)) 0 60px; }
    .details, .related, .discussion { padding: 0 12px; }
  }
  .watch.mini { display: block; padding: 0; max-width: none; }
  .watch.mini .details, .watch.mini .related, .watch.mini .discussion { display: none; }
  .minibar { position: absolute; top: 0; left: 0; right: 0; display: flex; align-items: center; gap: 2px; padding: 4px 6px 14px 10px;
    color: #fff; background: linear-gradient(rgba(0, 0, 0, 0.75), transparent); opacity: 0; transition: opacity 0.15s; z-index: 4; }
  .stage:hover .minibar, .minibar:focus-within { opacity: 1; }
  @media (hover: none) { .minibar { opacity: 1; } }       /* a touch screen has no hover: always show it */
  .minibar .mt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
  .minibar button { background: none; border: 0; color: #fff; font-size: 16px; padding: 4px 8px; border-radius: 6px; }
  .minibar button:hover { background: rgba(255, 255, 255, 0.2); }
  .autonext { position: absolute; inset: 0; z-index: 6; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
    background: rgba(0, 0, 0, 0.78); color: #fff; text-align: center; padding: 16px; }
  .an-label { font-size: 14px; opacity: 0.8; }
  .an-title { font-size: 18px; font-weight: 700; max-width: 40ch; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .an-actions { display: flex; gap: 10px; }
  .autonext.small .an-title { font-size: 13px; }
  .autonext.small .an-label { font-size: 12px; }
  .details { grid-area: details; min-width: 0; }
  .related { grid-area: related; margin-top: 24px; min-width: 0; }
  .discussion { grid-area: comments; min-width: 0; }
  @media (min-width: 1100px) {
    .watch { max-width: 1680px; grid-template-columns: minmax(0, 1fr) 380px;
      grid-template-areas: "stage related" "details related" "comments related"; }
    .related { margin-top: 0; }
    /* theater: the video takes the full width, the rest sits below it as before */
    .watch.theater { grid-template-areas: "stage stage" "details related" "comments related"; }
  }
  h1 { font-size: 20px; margin: 16px 0 6px; }
  h2 { font-size: 15px; margin: 0 0 8px; }
  .byline { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; }
  .spacer { flex: 1; }
  .chan { font-weight: 600; }
  section { margin-top: 18px; }
  .chapters { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 2px 16px; }
  .chapters button { width: 100%; display: flex; gap: 12px; text-align: left; background: none; border: 0; padding: 6px 8px;
    border-radius: 6px; }
  .chapters button:hover { background: var(--bg-hover); }
  .current button { background: var(--bg-elev); color: var(--accent); font-weight: 600; }
  .ts { color: var(--text-dim); font-variant-numeric: tabular-nums; min-width: 4.5ch; }
  .plist ol { list-style: none; padding: 0; margin: 0; max-height: 280px; overflow: auto;
    border: 1px solid var(--border); border-radius: var(--radius); }
  .plist a { display: flex; gap: 12px; padding: 7px 12px; align-items: baseline; }
  .plist a:hover { background: var(--bg-hover); }
  .plist li.current a { background: var(--bg-elev); color: var(--accent); font-weight: 600; }
  .plist .dur { margin-left: auto; font-variant-numeric: tabular-nums; }
  .desc { background: var(--bg-elev); border-radius: var(--radius); padding: 14px 16px; }
  .desc pre { font: inherit; white-space: pre-wrap; word-break: break-word; margin: 0 0 10px; max-height: 6.5em; overflow: hidden; }
  .desc.open pre { max-height: none; }
</style>
