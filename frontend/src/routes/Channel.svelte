<script>
  import { onMount, onDestroy } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { subscribers, img, ago } from '../lib/format.js'
  import { uid } from '../lib/tracker.js'
  import { loadSubs } from '../lib/nav.svelte.js'
  import VideoCard from '../components/VideoCard.svelte'
  import PlaylistResult from '../components/PlaylistResult.svelte'

  const id = router.pathname.split('/')[2]
  const SORTS = [['newest', 'Newest'], ['popular', 'Popular'], ['oldest', 'Oldest']]
  const sort = SORTS.some(([k]) => k === router.query.get('sort')) ? router.query.get('sort') : 'newest'
  const tab = router.query.get('tab') === 'playlists' ? 'playlists' : 'videos'
  const PAGE = 60
  const requestId = uid()

  let header = $state(null)        // channel name, avatar, ...
  let videos = $state([])          // everything loaded so far, in order
  let subscribed = $state(false)
  let error = $state('')
  let busy = $state(false)
  let loading = $state(false)
  let hasMore = $state(false)
  let scanning = $state(null)      // { done, total } while the channel's uploads are being read
  let notice = $state('')
  let unavailable = $state('')
  let meta = $state(null)          // { total, at, checked } for sorted lists
  let refreshing = $state(false)   // the list is being re-read in the background (you keep seeing the old one)
  let timer = null
  let playlists = $state(null)       // the playlists the channel has made (its Playlists tab); null until loaded
  let playlistsError = $state('')
  let find = $state('')              // search inside this channel's saved videos (local: no request to YouTube)
  let found = $state(null)           // { videos, searched } for the current search
  let findTimer
  function onFind() {
    clearTimeout(findTimer)
    const q = find.trim()
    if (!q) { found = null; return }
    findTimer = setTimeout(async () => {
      try { found = await api.get(`/api/channel/${id}/search?q=${encodeURIComponent(q)}`) } catch { found = { videos: [], searched: 0 } }
    }, 250)
  }
  let sentinel = $state()

  // Fetch one page. `first` starts the list over; otherwise the page is appended.
  async function fetchPage(first, refresh = false) {
    if (loading) return
    loading = true; error = ''
    try {
      const offset = first ? 0 : videos.length
      const r = await api.get(`/api/channel/${id}?sort=${sort}&offset=${offset}&limit=${PAGE}${refresh ? '&refresh=true' : ''}`)
      header = r.channel; subscribed = r.subscribed
      document.title = `${header.title} · newtube`
      if (r.scanning) {                        // reading the whole upload list: check back in a moment
        scanning = r.scanning
        timer = setTimeout(() => { loading = false; fetchPage(true) }, 1500)
        return
      }
      scanning = null
      const known = new Set(first ? [] : videos.map((v) => v.id))
      videos = first ? r.videos : [...videos, ...r.videos.filter((v) => !known.has(v.id))]
      hasMore = r.has_more
      notice = r.notice ?? ''; unavailable = r.unavailable ?? ''
      meta = r.total_videos ? { total: r.total_videos, at: r.fetched_at, checked: r.checked_at } : null
      refreshing = !!r.refreshing
    } catch (e) {
      error = e.message
    }
    loading = false
  }

  async function loadPlaylists() {
    try {
      playlists = await api.get(`/api/channel/${id}/playlists`)
    } catch (e) {
      playlistsError = e.message
    }
  }
  onMount(() => { fetchPage(true); if (tab === 'playlists') loadPlaylists() })

  // Oldest/Popular are served from a list we already hold, so scrolling loads more at no cost.
  // (Newest asks YouTube each time, so it keeps an explicit button.) The observer is re-created after
  // every page: observing starts with a fresh "is it visible?" report, which keeps a short page from
  // leaving the sentinel in view and the list stuck.
  $effect(() => {
    videos.length                                    // re-run whenever a page was added
    if (sort === 'newest' || !sentinel) return
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting && hasMore && !loading && !scanning) fetchPage(false) },
      { rootMargin: '600px' })
    io.observe(sentinel)
    return () => io.disconnect()
  })
  onDestroy(() => clearTimeout(timer))

  function rescan() {
    clearTimeout(timer)
    videos = []; hasMore = false; meta = null
    fetchPage(true, true)
  }

  async function toggleSub() {
    busy = true
    try {
      if (subscribed) { await api.del(`/api/subscriptions/${id}`); subscribed = false }
      else { await api.post('/api/subscriptions', { ref: id }); subscribed = true }
      loadSubs()                                     // keep the sidebar list in step
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="page">
  {#if error}<p class="error">{error} <button class="btn" onclick={() => fetchPage(true)}>Try again</button></p>{/if}
  {#if !header && !error}<p class="muted">Loading…</p>{/if}
  {#if header}
    <header class="head">
      {#if header.avatar_url}
        <img src={img(header.avatar_url)} alt="" />
      {:else}
        <span class="ph">{header.title[0]}</span>
      {/if}
      <div class="meta">
        <h1>{header.title}</h1>
        <div class="muted">{[header.handle, subscribers(header.subscriber_count)].filter(Boolean).join(' · ')}</div>
        {#if header.description}<p class="muted desc">{header.description}</p>{/if}
      </div>
      <button class="btn" class:primary={!subscribed} disabled={busy} onclick={toggleSub}>
        {subscribed ? 'Subscribed ✓' : 'Subscribe'}
      </button>
    </header>

    <nav class="chips tabs" aria-label="Channel">
      <a href={`/channel/${id}`} class="chip" class:active={tab === 'videos'}>Videos</a>
      <a href={`/channel/${id}?tab=playlists`} class="chip" class:active={tab === 'playlists'}>Playlists</a>
    </nav>

    {#if tab === 'videos'}
      <input class="input find" type="search" bind:value={find} oninput={onFind} placeholder="Search this channel's saved videos" aria-label="Search this channel" maxlength="200" />
      <div class="chips sorts" role="group" aria-label="Sort videos">
        {#each SORTS as [k, label]}
          <a href={`/channel/${id}${k === 'newest' ? '' : '?sort=' + k}`} class="chip" class:active={k === sort}>{label}</a>
        {/each}
      </div>
    {/if}

    {#if tab === 'playlists'}
      {#if playlistsError}<p class="error">{playlistsError} <button class="btn" onclick={() => { playlistsError = ''; loadPlaylists() }}>Try again</button></p>
      {:else if !playlists}<p class="muted">Loading…</p>
      {:else if !playlists.length}<p class="empty">This channel has no playlists.</p>
      {:else}
        <div class="grid">
          {#each playlists as pl, i (pl.id)}<PlaylistResult playlist={pl} position={i} />{/each}
        </div>
      {/if}
    {:else if found}
      <p class="muted small">
        {found.videos.length ? `${found.videos.length} match${found.videos.length === 1 ? '' : 'es'}` : 'No matches'} among the {found.searched.toLocaleString()} video{found.searched === 1 ? '' : 's'} saved on this server.
        {#if found.searched < 60}Sort by Oldest or Popular once to save every upload of this channel, then this searches all of them.{/if}
      </p>
      <div class="grid">
        {#each found.videos as v, i (v.id)}
          <VideoCard video={v} surface="channel" position={i} {requestId} />
        {/each}
      </div>
    {:else}
    {#if scanning}
      <div class="scan" role="status">
        <b>Reading this channel's uploads…</b>
        {scanning.done.toLocaleString()}{scanning.total ? ` of ${scanning.total.toLocaleString()}` : ''} videos.
        <span class="muted">This happens once per channel: the list is then saved on this server and only topped up with new uploads, so
        {sort === 'oldest' ? 'Oldest' : 'Popular'} can cover every video. You can leave this page; it carries on.</span>
        {#if scanning.total}<progress max={scanning.total} value={scanning.done}></progress>{/if}
      </div>
    {/if}

    {#if unavailable}
      <p class="muted">{unavailable} <a class="link" href={`/channel/${id}`}>Show newest instead</a></p>
    {:else if !videos.length && !loading && !scanning && !error}
      <p class="empty">No videos found.</p>
    {/if}

    <div class="grid">
      {#each videos as v, i (v.id)}
        <VideoCard video={v} surface="channel" position={i} {requestId} />
      {/each}
    </div>

    {#if loading && !scanning && videos.length}<p class="muted">Loading…</p>{/if}
    {#if hasMore && !scanning}
      {#if sort === 'newest'}
        <p><button class="btn" onclick={() => fetchPage(false)} disabled={loading}>Load more videos</button></p>
      {:else}
        <div bind:this={sentinel} class="sentinel" aria-hidden="true"></div>
      {/if}
    {/if}

    {#if notice}<p class="muted small">{notice}</p>{/if}
    {#if meta}
      <p class="muted small">
        {sort === 'oldest' ? 'All' : 'Ranked over'} {meta.total.toLocaleString()} videos · saved {ago(meta.at)}{meta.checked ? ` · checked for new uploads ${ago(meta.checked)}` : ''} ·
        <button class="link" onclick={rescan}>Rescan</button>
        {#if refreshing}<span> · refreshing the list in the background…</span>{/if}
      </p>
    {/if}
    {/if}
  {/if}
</div>

<style>
  .head { display: flex; gap: 24px; align-items: center; margin-bottom: 24px; }
  .head img, .ph { width: 96px; height: 96px; border-radius: 50%; object-fit: cover; flex: none; }
  .ph { background: var(--bg-hover); display: grid; place-items: center; font-size: 36px; font-weight: 700; }
  .meta { flex: 1; min-width: 0; }
  .meta h1 { margin: 0 0 4px; }
  .desc { margin: 8px 0 0; max-width: 70ch; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tabs { margin-bottom: 14px; }
  .sorts { margin-bottom: 20px; }
  .find { width: min(100%, 360px); margin-bottom: 14px; display: block; }
  .scan { background: var(--bg-elev); border-radius: var(--radius); padding: 14px 18px; margin-bottom: 20px; display: flex; flex-direction: column; gap: 8px; }
  .scan progress { width: 100%; accent-color: var(--accent); }
  .sentinel { height: 1px; }
  button.link { color: var(--accent); background: none; border: 0; padding: 0; }
</style>
