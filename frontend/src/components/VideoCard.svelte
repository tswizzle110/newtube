<script>
  import { duration, ago, views } from '../lib/format.js'
  import { impression, trackClick, trackEvent } from '../lib/tracker.js'
  import { navigate } from '../lib/router.svelte.js'

  // extra: query string appended to the watch link (e.g. '&list=PL...'); searchQuery: set on search pages
  // why: {score, reasons[{label, points}]} for recommended cards; ondismiss(type): 'not_interested' | 'dont_recommend_channel'
  let { video, surface, position, requestId, extra = '', searchQuery = null, why = null, ondismiss = null, onremove = null } = $props()
  let showWhy = $state(false)
  const href = $derived(`/watch?v=${video.id}&src=${surface}${extra}`)

  const pct = $derived(
    video.duration && video.resume_position ? Math.min(100, (video.resume_position / video.duration) * 100) : 0,
  )
  let imgFailed = $state(false)

  function open(e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
    e.preventDefault()
    trackClick({ video_id: video.id, surface, position, request_id: requestId })
    if (searchQuery) {
      trackEvent('search_click', { video_id: video.id, payload: { q: searchQuery, kind: 'video', position } })
    }
    navigate(href)
  }
</script>

<article class="card" use:impression={{ videoId: video.id, surface, position, requestId }}>
  <a class="thumb" {href} onclick={open} tabindex="-1" aria-hidden="true">
    {#if !imgFailed}
      <img src={`/api/thumb/${video.id}/mqdefault`} alt="" loading="lazy" onerror={() => (imgFailed = true)} />
    {/if}
    {#if video.duration}<span class="dur">{duration(video.duration)}</span>{/if}
    {#if video.completed}<span class="done" title="Watched">✓</span>
    {:else if video.seen}<span class="seen" title="In your watch history (not necessarily finished)">Watched before</span>{/if}
    {#if pct > 0 && !video.completed}<span class="bar"><span style:width={pct + '%'}></span></span>{/if}
  </a>
  <div class="meta">
    <a class="title" {href} onclick={open} title={video.title}>{video.title}</a>
    {#if video.channel_id}
      <a class="chan muted" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
    {:else}
      <span class="chan muted">{video.channel_title}</span>
    {/if}
    <div class="muted small">
      {[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}
    </div>
    {#if onremove}
      <div class="actions small"><button class="act" onclick={onremove}>Remove from this list</button></div>
    {/if}
    {#if why}
      <div class="actions small">
        <button class="act" aria-expanded={showWhy} onclick={() => (showWhy = !showWhy)}>Why this?</button>
        {#if ondismiss}
          <button class="act" onclick={() => ondismiss('not_interested')}>Not interested</button>
          {#if video.channel_id}<button class="act" onclick={() => ondismiss('dont_recommend_channel')}>Never this channel</button>{/if}
        {/if}
      </div>
      {#if showWhy}
        <ul class="why small" aria-label="Why this was recommended">
          {#each why.reasons as r}
            <li><span>{r.label}</span><b class:neg={r.points < 0}>{r.points > 0 ? '+' : ''}{r.points.toFixed(2)}</b></li>
          {/each}
          <li class="total"><span>Score</span><b>{why.score.toFixed(2)}</b></li>
        </ul>
      {/if}
    {/if}
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
  .seen { position: absolute; left: 6px; top: 6px; background: rgba(0, 0, 0, 0.75); color: var(--muted, #aaa);
    border-radius: 4px; padding: 0 5px; font-size: 11px; }
  .bar { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: rgba(255, 255, 255, 0.25); }
  .bar span { display: block; height: 100%; background: var(--accent); }
  .meta { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .title { font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; }
  .chan:hover { color: var(--text); }
  .small { font-size: 13px; }
  .actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 2px; }
  .act { background: none; border: 0; padding: 0; color: var(--text-dim); font: inherit; cursor: pointer; }
  .act:hover { color: var(--text); text-decoration: underline; }
  .why { list-style: none; margin: 4px 0 0; padding: 8px 10px; background: var(--bg-elev); border-radius: 8px; }
  .why li { display: flex; justify-content: space-between; gap: 12px; padding: 1px 0; }
  .why b { font-variant-numeric: tabular-nums; color: #7ee787; }
  .why b.neg { color: #ff7b72; }
  .why .total { border-top: 1px solid rgba(128, 128, 128, 0.3); margin-top: 4px; padding-top: 4px; }
  .why .total b { color: var(--text); }
</style>
