<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router, navigate } from '../lib/router.svelte.js'
  import { duration as fmt, ago, views } from '../lib/format.js'
  import Player from '../components/Player.svelte'
  import Comments from '../components/Comments.svelte'

  const id = router.query.get('v')
  const source = router.query.get('src') ?? 'direct'
  const list = router.query.get('list')   // present when playing from a playlist
  let pl = $state(null)

  let video = $state(null)
  let error = $state('')
  let player = $state()
  let t = $state(0)
  let expanded = $state(false)

  onMount(() => {
    let timer
    // Start the slow parts in parallel with the metadata request instead of after it:
    // the player library chunk, and the manifest (the server then extracts the video and probes the
    // stream layouts once; the player's own manifest request a moment later is a cheap cache hit).
    import('shaka-player/dist/shaka-player.dash.js').catch(() => {})
    fetch(`/api/dash/${id}.mpd`, { priority: 'low' }).catch(() => {})
    if (list) api.get(`/api/playlist/${list}`).then((p) => (pl = p)).catch(() => {})
    api.get(`/api/video/${id}`).then((v) => {
      video = v
      document.title = `${v.title} · newtube`
      timer = setInterval(() => { t = player?.time ?? 0 }, 500) // for highlighting the current chapter
    }).catch((e) => (error = e.message))
    return () => { clearInterval(timer); document.title = 'newtube' }
  })

  const current = $derived(video?.chapters?.findLastIndex((c) => c.start <= t) ?? -1)
  const at = $derived(pl ? pl.videos.findIndex((v) => v.id === id) : -1)
  const next = $derived(at >= 0 ? pl.videos[at + 1] : null)
  const watchHref = (vid) => `/watch?v=${vid}&src=playlist&list=${list}`
  // Playlist autoplay: when this video ends, go to the next one.
  const advance = () => { if (next) navigate(watchHref(next.id)) }
</script>

<div class="watch">
  {#if error}
    <p class="error">{error}</p>
    <a href="/" class="muted">Back to subscriptions</a>
  {:else if !video}
    <p class="muted">Loading…</p>
  {:else}
    <Player {video} {source} startAt={video.resume_position} bind:api={player} onended={list ? advance : null} />
    <h1>{video.title}</h1>
    <div class="byline">
      <a class="chan" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
      <span class="muted">{[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}</span>
    </div>

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

    <Comments videoId={video.id} {player} />
  {/if}
</div>

<style>
  .watch { max-width: 1280px; margin: 0 auto; padding: 20px 24px 80px; }
  h1 { font-size: 20px; margin: 16px 0 6px; }
  h2 { font-size: 15px; margin: 0 0 8px; }
  .byline { display: flex; gap: 14px; align-items: baseline; margin-bottom: 18px; }
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
