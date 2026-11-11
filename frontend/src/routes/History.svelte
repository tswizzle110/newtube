<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import { nearEnd } from '../lib/scroll.js'
  import VideoCard from '../components/VideoCard.svelte'
  import { historyNote } from '../lib/historyNote.js'

  const PAGE = 60
  const requestId = uid()
  let items = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')
  let q = $state('')
  let applied = $state('') // the search the list on screen belongs to

  async function load(reset = false) {
    if (!reset && (loading || done)) return
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

  // The list narrows as you type (no Search button to press).
  let typing
  function onType() { clearTimeout(typing); typing = setTimeout(() => load(true), 350) }

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
  <div class="page-head">
    <h1>History</h1>
    <span class="spacer"></span>
    <input class="input find" type="search" bind:value={q} oninput={onType} placeholder="Search your history" aria-label="Search your history" maxlength="100" />
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !items.length && !error}
    <p class="empty">{applied ? 'Nothing in your history matches that.' : 'Nothing in your history yet.'}</p>
  {/if}
  {#each groups as g (g.label + g.videos[0].id)}
    <h2 class="section">{g.label}</h2>
    <div class="grid">
      {#each g.videos as v (v.id)}
        <VideoCard video={{ ...v, title: v.title || v.id }} surface="history" position={items.indexOf(v)} {requestId}
                   note={historyNote(v, timeOf)} showSeen={false} onremove={() => remove(v)} removeLabel="Remove from history" />
      {/each}
    </div>
  {/each}
  {#if loading}<p class="muted">Loading…</p>{/if}
  {#if !loading && !done && items.length}<div use:nearEnd={() => load()}></div>{/if}
</div>

<style>
  .find { width: min(100%, 320px); }
</style>
