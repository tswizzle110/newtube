<script>
  import { duration, ago, views } from '../lib/format.js'
  import { impression, trackClick, trackEvent } from '../lib/tracker.js'
  import { navigate } from '../lib/router.svelte.js'
  import Menu from './Menu.svelte'

  // extra: query string appended to the watch link (e.g. '&list=PL...'); searchQuery: set on search pages
  // why: {score, reasons[{label, points}]} for recommended cards; ondismiss(type): 'not_interested' | 'dont_recommend_channel'
  let { video, surface, position, requestId, extra = '', searchQuery = null, why = null, ondismiss = null, onremove = null, removeLabel = 'Remove from this list', note = '', onup = null, ondown = null, compact = false, showSeen = true } = $props()
  let showWhy = $state(false)
  const href = $derived(`/watch?v=${video.id}&src=${surface}${extra}`)

  const pct = $derived(
    video.duration && video.resume_position ? Math.min(100, (video.resume_position / video.duration) * 100) : 0,
  )
  // The sharp picture first; if it cannot be had for any reason (an older server, a missing size, a network hiccup)
  // fall back to the small one rather than showing nothing.
  let thumbSize = $state(compact ? 'mqdefault' : 'best')
  let imgFailed = $state(false)
  const thumbFailed = () => (thumbSize === 'best' ? (thumbSize = 'mqdefault') : (imgFailed = true))

  // The rare actions live behind the ⋯ button, so the grid itself stays clean.
  const items = $derived([
    ...(why ? [{ label: showWhy ? 'Hide why this' : 'Why this?', onclick: () => (showWhy = !showWhy) }] : []),
    ...(why && ondismiss ? [{ label: 'Not interested', onclick: () => ondismiss('not_interested') }] : []),
    ...(why && ondismiss && video.channel_id ? [{ label: 'Never this channel', danger: true, onclick: () => ondismiss('dont_recommend_channel') }] : []),
    ...(onremove ? [{ label: removeLabel, onclick: onremove }] : []),
    ...(onremove && onup ? [{ label: 'Move earlier', onclick: onup }] : []),
    ...(onremove && ondown ? [{ label: 'Move later', onclick: ondown }] : []),
  ])

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

<article class="card" class:compact use:impression={{ videoId: video.id, surface, position, requestId }}>
  <a class="thumb" {href} onclick={open} tabindex="-1" aria-hidden="true">
    {#if !imgFailed}
      <img src={`/api/thumb/${video.id}/${thumbSize}`} alt="" loading="lazy" onerror={thumbFailed} />
    {/if}
    {#if video.duration}<span class="dur">{duration(video.duration)}</span>{/if}
    {#if video.completed}<span class="done" title="Watched">✓</span>
    {:else if video.seen && showSeen && !(pct > 0)}<span class="seen" title="In your watch history (not necessarily finished)">Watched before</span>{/if}
    {#if pct > 0 && !video.completed}<span class="bar"><span style:width={pct + '%'}></span></span>{/if}
  </a>
  <div class="meta">
    <div class="titlerow">
      <a class="title" {href} onclick={open} title={video.title}>{video.title}</a>
      <Menu {items} />
    </div>
    {#if video.channel_id}
      <a class="chan muted" href={`/channel/${video.channel_id}`}>{video.channel_title}</a>
    {:else}
      <span class="chan muted">{video.channel_title}</span>
    {/if}
    <div class="muted small">
      {[views(video.view_count), ago(video.published_at)].filter(Boolean).join(' · ')}
    </div>
    {#if note}<div class="muted small">{note}</div>{/if}
    {#if why && showWhy}
      <ul class="why small" aria-label="Why this was recommended">
        {#each why.reasons as r}
          <li><span>{r.label}</span><b class:neg={r.points < 0}>{r.points > 0 ? '+' : ''}{r.points.toFixed(2)}</b></li>
        {/each}
        <li class="total"><span>Score</span><b>{why.score.toFixed(2)}</b></li>
      </ul>
    {/if}
  </div>
</article>

<style>
  .card { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  /* a small row (thumbnail left, text right), for the Up next column */
  .card.compact { flex-direction: row; gap: 10px; align-items: flex-start; }
  .compact .thumb { width: 168px; flex: none; }
  .compact .title { font-size: 14px; -webkit-line-clamp: 2; }
  .compact .meta { flex: 1; }
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
  .titlerow { display: flex; align-items: flex-start; gap: 4px; }
  .titlerow :global(.menu) { margin: -4px -6px -4px 0; }
  @media (pointer: coarse) { .titlerow :global(.menu) { margin: -11px -11px -11px 0; } }       /* (a bigger button must not make the row taller) */
  .title { flex: 1; min-width: 0; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; }
  .chan:hover { color: var(--text); }
  .why { list-style: none; margin: 4px 0 0; padding: 8px 10px; background: var(--bg-elev); border-radius: 8px; }
  .why li { display: flex; justify-content: space-between; gap: 12px; padding: 1px 0; }
  .why b { font-variant-numeric: tabular-nums; color: #7ee787; }
  .why b.neg { color: #ff7b72; }
  .why .total { border-top: 1px solid rgba(128, 128, 128, 0.3); margin-top: 4px; padding-top: 4px; }
  .why .total b { color: var(--text); }
</style>
