<script>
  import { duration, ago, views } from '../lib/format.js'
  import { impression, trackClick } from '../lib/tracker.js'
  import { navigate } from '../lib/router.svelte.js'

  let { video, surface, position, requestId } = $props()

  const pct = $derived(
    video.duration && video.resume_position ? Math.min(100, (video.resume_position / video.duration) * 100) : 0,
  )
  let imgFailed = $state(false)

  function open(e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
    e.preventDefault()
    trackClick({ video_id: video.id, surface, position, request_id: requestId })
    navigate(`/watch?v=${video.id}&src=${surface}`)
  }
</script>

<article class="card" use:impression={{ videoId: video.id, surface, position, requestId }}>
  <a class="thumb" href={`/watch?v=${video.id}&src=${surface}`} onclick={open} tabindex="-1" aria-hidden="true">
    {#if !imgFailed}
      <img src={`/api/thumb/${video.id}/mqdefault`} alt="" loading="lazy" onerror={() => (imgFailed = true)} />
    {/if}
    {#if video.duration}<span class="dur">{duration(video.duration)}</span>{/if}
    {#if video.completed}<span class="done" title="Watched">✓</span>{/if}
    {#if pct > 0 && !video.completed}<span class="bar"><span style:width={pct + '%'}></span></span>{/if}
  </a>
  <div class="meta">
    <a class="title" href={`/watch?v=${video.id}&src=${surface}`} onclick={open} title={video.title}>{video.title}</a>
    <a class="chan muted" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
    <div class="muted small">
      {[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}
    </div>
  </div>
</article>

<style>
  .card { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .thumb { position: relative; display: block; aspect-ratio: 16 / 9; background: var(--bg-elev);
    border-radius: var(--radius); overflow: hidden; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .dur { position: absolute; right: 6px; bottom: 8px; background: rgba(0, 0, 0, 0.8); color: #fff;
    font-size: 12px; padding: 1px 5px; border-radius: 4px; font-variant-numeric: tabular-nums; }
  .done { position: absolute; left: 6px; top: 6px; background: rgba(0, 0, 0, 0.75); color: #7ee787;
    border-radius: 4px; padding: 0 5px; font-size: 12px; }
  .bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(255, 255, 255, 0.25); }
  .bar span { display: block; height: 100%; background: var(--accent); }
  .meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .title { font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; }
  .chan:hover { color: var(--text); }
  .small { font-size: 13px; }
</style>
