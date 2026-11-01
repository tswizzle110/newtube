<script>
  import { img } from '../lib/format.js'
  import { trackEvent } from '../lib/tracker.js'
  import { navigate } from '../lib/router.svelte.js'

  let { playlist, searchQuery = null, position = 0 } = $props()
  const href = $derived(`/playlist?list=${playlist.id}`)

  function open(e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
    e.preventDefault()
    trackEvent('search_click', { payload: { q: searchQuery, kind: 'playlist', position, id: playlist.id } })
    navigate(href)
  }
</script>

<a class="card" {href} onclick={open}>
  <div class="thumb">
    {#if playlist.thumbnail_url}<img src={img(playlist.thumbnail_url)} alt="" loading="lazy" />{/if}
    <span class="badge">☰ Playlist{playlist.video_count ? ` · ${playlist.video_count}` : ''}</span>
  </div>
  <div class="title">{playlist.title}</div>
  <div class="muted small">{playlist.channel_title ?? ''}</div>
</a>

<style>
  .card { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .thumb { position: relative; aspect-ratio: 16 / 9; background: var(--bg-elev); border-radius: var(--radius); overflow: hidden; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .badge { position: absolute; right: 6px; bottom: 8px; background: rgba(0, 0, 0, 0.8); color: #fff; font-size: 12px;
    padding: 1px 6px; border-radius: 4px; }
  .title { font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .small { font-size: 13px; }
</style>
