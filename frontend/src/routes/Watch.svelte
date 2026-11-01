<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { duration as fmt, ago, views } from '../lib/format.js'
  import Player from '../components/Player.svelte'

  const id = router.query.get('v')
  const source = router.query.get('src') ?? 'direct'

  let video = $state(null)
  let error = $state('')
  let player = $state()
  let t = $state(0)
  let expanded = $state(false)

  onMount(() => {
    let timer
    api.get(`/api/video/${id}`).then((v) => {
      video = v
      document.title = `${v.title} · newtube`
      timer = setInterval(() => { t = player?.time ?? 0 }, 500) // for highlighting the current chapter
    }).catch((e) => (error = e.message))
    return () => { clearInterval(timer); document.title = 'newtube' }
  })

  const current = $derived(video?.chapters?.findLastIndex((c) => c.start <= t) ?? -1)
</script>

<div class="watch">
  {#if error}
    <p class="error">{error}</p>
    <a href="/" class="muted">Back to subscriptions</a>
  {:else if !video}
    <p class="muted">Loading…</p>
  {:else}
    <Player {video} {source} startAt={video.resume_position} bind:api={player} />
    <h1>{video.title}</h1>
    <div class="byline">
      <a class="chan" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
      <span class="muted">{[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}</span>
    </div>

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
  .desc { background: var(--bg-elev); border-radius: var(--radius); padding: 14px 16px; }
  .desc pre { font: inherit; white-space: pre-wrap; word-break: break-word; margin: 0 0 10px; max-height: 6.5em; overflow: hidden; }
  .desc.open pre { max-height: none; }
</style>
