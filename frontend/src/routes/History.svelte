<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'

  const PAGE = 60
  const requestId = uid()
  let items = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')
  let q = $state('')
  let applied = '' // the search the list on screen belongs to

  async function load(reset = false) {
    if (reset) { items = []; done = false; applied = q.trim() }
    loading = true; error = ''
    try {
      const page = await api.get(`/api/history?limit=${PAGE}&offset=${items.length}&q=${encodeURIComponent(applied)}`)
      items.push(...page)
      if (page.length < PAGE) done = true
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => { document.title = 'History · newtube'; load(true) })

  async function remove(v) {
    try {
      await api.del(`/api/history/${v.id}`)
      items = items.filter((x) => x.id !== v.id)
    } catch (e) {
      error = e.message
    }
  }

  // "Today", "Yesterday", then the date, in your own time zone.
  function dayLabel(ts) {
    const d = new Date(ts * 1000)
    const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
    const days = Math.round((start(new Date()) - start(d)) / 86400000)
    if (days === 0) return 'Today'
    if (days === 1) return 'Yesterday'
    return d.toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
  }
  const timeOf = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

  // Consecutive items with the same day label share a heading.
  const groups = $derived.by(() => {
    const out = []
    for (const v of items) {
      const label = dayLabel(v.watched_at)
      if (!out.length || out.at(-1).label !== label) out.push({ label, videos: [] })
      out.at(-1).videos.push(v)
    }
    return out
  })
</script>

<div class="page">
  <h1>Watch history</h1>
  <form class="find" onsubmit={(e) => { e.preventDefault(); load(true) }} role="search">
    <input class="input" bind:value={q} placeholder="Search your history" aria-label="Search your history" maxlength="100" />
    <button class="btn">Search</button>
  </form>
  <p class="muted small">Only on this server. Removing a video here also forgets where you stopped and stops it counting towards recommendations.</p>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !items.length && !error}
    <p class="muted">{applied ? 'Nothing in your history matches that.' : 'Nothing in your history yet.'}</p>
  {/if}
  {#each groups as g (g.label + g.videos[0].id)}
    <h2>{g.label}</h2>
    <div class="grid">
      {#each g.videos as v (v.id)}
        <VideoCard video={{ ...v, title: v.title || v.id }} surface="history" position={items.indexOf(v)} {requestId}
                   note={`Watched ${timeOf(v.watched_at)}`} onremove={() => remove(v)} removeLabel="Remove from history" />
      {/each}
    </div>
  {/each}
  {#if loading}<p class="muted">Loading…</p>{/if}
  {#if !loading && !done && items.length}
    <p><button class="btn" onclick={() => load()}>Load more</button></p>
  {/if}
</div>

<style>
  h1 { margin: 0 0 12px; }
  h2 { font-size: 16px; margin: 28px 0 12px; }
  .find { display: flex; gap: 8px; max-width: 520px; margin-bottom: 8px; }
  .find .input { flex: 1; }
  .small { font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
