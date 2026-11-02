<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router, navigate } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'
  import ChannelResult from '../components/ChannelResult.svelte'
  import PlaylistResult from '../components/PlaylistResult.svelte'

  const q = router.query.get('q') ?? ''
  const kind = ['video', 'channel', 'playlist'].includes(router.query.get('kind')) ? router.query.get('kind') : 'video'
  const requestId = uid()
  const TABS = [['video', 'Videos'], ['channel', 'Channels'], ['playlist', 'Playlists']]
  const enc = encodeURIComponent(q)

  // Filters for a video search (each page of results is its own address, so a filter is a link you can share).
  const DATES = [['', 'Any time'], ['hour', 'Last hour'], ['today', 'Today'], ['week', 'This week'], ['month', 'This month'], ['year', 'This year']]
  const DURATIONS = [['', 'Any length'], ['short', 'Under 4 minutes'], ['medium', '4 to 20 minutes'], ['long', 'Over 20 minutes']]
  const SORTS = [['', 'Relevance'], ['date', 'Upload date'], ['views', 'View count']]
  const pick = (name, options) => (options.some(([v]) => v === router.query.get(name)) ? router.query.get(name) : '')
  let date = $state(pick('date', DATES))
  let duration = $state(pick('duration', DURATIONS))
  let sort = $state(pick('sort', SORTS))
  const params = (d, du, s) => `${d ? `&date=${d}` : ''}${du ? `&duration=${du}` : ''}${s ? `&sort=${s}` : ''}`
  const filterQuery = params(date, duration, sort)
  const refilter = () => navigate(`/search?q=${enc}&kind=video${params(date, duration, sort)}`)

  let results = $state([])
  let loading = $state(true)
  let error = $state('')

  onMount(async () => {
    document.title = `${q} · newtube`
    try {
      const r = await api.get(`/api/search?q=${enc}&kind=${kind}&limit=30${kind === 'video' ? filterQuery : ''}`)
      results = r.results
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  })
</script>

<div class="page">
  <nav class="tabs">
    {#each TABS as [k, label]}
      <a href={`/search?q=${enc}&kind=${k}`} class:active={k === kind}>{label}</a>
    {/each}
  </nav>
  {#if kind === 'video'}
    <div class="filters" role="group" aria-label="Search filters">
      <label>Uploaded <select bind:value={date} onchange={refilter} aria-label="Upload date">{#each DATES as [v, label]}<option value={v}>{label}</option>{/each}</select></label>
      <label>Length <select bind:value={duration} onchange={refilter} aria-label="Duration">{#each DURATIONS as [v, label]}<option value={v}>{label}</option>{/each}</select></label>
      <label>Sort by <select bind:value={sort} onchange={refilter} aria-label="Sort by">{#each SORTS as [v, label]}<option value={v}>{label}</option>{/each}</select></label>
    </div>
  {/if}
  {#if error}<p class="error">{error}</p>{/if}
  {#if loading}<p class="muted">Searching…</p>{/if}
  {#if !loading && !error && !results.length}<p class="muted">No {kind}s found for “{q}”.</p>{/if}

  {#if kind === 'video'}
    <div class="grid">
      {#each results as v, i (v.id)}
        <VideoCard video={v} surface="search" position={i} {requestId} searchQuery={q} />
      {/each}
    </div>
  {:else if kind === 'channel'}
    <div class="list">
      {#each results as c, i (c.id)}<ChannelResult channel={c} searchQuery={q} position={i} />{/each}
    </div>
  {:else}
    <div class="grid">
      {#each results as p, i (p.id)}<PlaylistResult playlist={p} searchQuery={q} position={i} />{/each}
    </div>
  {/if}
</div>

<style>
  .filters { display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 18px; }
  .filters label { display: flex; gap: 8px; align-items: center; color: var(--text-dim); font-size: 14px; }
  .tabs { display: flex; gap: 6px; margin-bottom: 20px; }
  .tabs a { padding: 6px 14px; border-radius: 999px; background: var(--bg-elev); color: var(--text-dim); }
  .tabs a:hover { background: var(--bg-hover); color: var(--text); }
  .tabs a.active { background: var(--text); color: var(--bg); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
  .list { max-width: 860px; }
</style>
