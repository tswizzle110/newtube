<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import { nearEnd } from '../lib/scroll.js'
  import { dayLabel } from '../lib/format.js'
  import { markSubsSeen } from '../lib/nav.svelte.js'
  import VideoCard from '../components/VideoCard.svelte'

  const PAGE = 48
  let videos = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')
  const requestId = uid() // groups every card shown during this visit to the feed

  // Narrowing what is shown (the filters only look at what is loaded; more loads as you scroll).
  let hideWatched = $state(readFlag())
  let channelFilter = $state('')
  function readFlag() { try { return localStorage.getItem('subsHideWatched') === '1' } catch { return false } }
  function toggleHide() {
    hideWatched = !hideWatched
    try { localStorage.setItem('subsHideWatched', hideWatched ? '1' : '0') } catch { /* private mode */ }
  }
  const channels = $derived([...new Map(videos.filter((v) => v.channel_id).map((v) => [v.channel_id, v.channel_title])).entries()]
    .sort((a, b) => (a[1] || '').localeCompare(b[1] || '')))
  const shown = $derived(videos.filter((v) => (!hideWatched || !(v.seen || v.completed)) && (!channelFilter || v.channel_id === channelFilter)))
  // Under a heading per day: Today, Yesterday, then the date.
  const days = $derived.by(() => {
    const out = []
    for (const v of shown) {
      const label = dayLabel(v.published_at)
      if (!out.length || out.at(-1).label !== label) out.push({ label, videos: [] })
      out.at(-1).videos.push(v)
    }
    return out
  })

  async function load() {
    if (loading && videos.length) return
    loading = true; error = ''
    try {
      const before = videos.length ? `&before=${videos.at(-1).published_at}` : ''
      const page = await api.get(`/api/feed?limit=${PAGE}${before}`)
      videos.push(...page)
      if (page.length < PAGE) done = true
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => { document.title = 'Subscriptions · newtube'; load(); markSubsSeen() })
</script>

<div class="page">
  <div class="page-head">
    <h1>Subscriptions</h1>
    <span class="spacer"></span>
    {#if channels.length > 1}
      <select bind:value={channelFilter} aria-label="Show one channel">
        <option value="">All channels</option>
        {#each channels as [id, title]}<option value={id}>{title}</option>{/each}
      </select>
    {/if}
    <button class="chip" class:active={hideWatched} aria-pressed={hideWatched} onclick={toggleHide}>Hide watched</button>
    <a class="btn" href="/channels">Manage channels</a>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !videos.length && !error}
    <p class="empty">Nothing here yet. <a href="/channels" class="link">Add some channels</a> to build your feed.</p>
  {/if}
  {#if videos.length && !shown.length && !loading}<p class="empty">Nothing to show with these filters{!done ? ' yet; more loads as you scroll' : ''}.</p>{/if}
  {#each days as g (g.label)}
    <h2 class="section">{g.label}</h2>
    <div class="grid">
      {#each g.videos as v (v.id)}
        <VideoCard video={v} surface="subs" position={videos.indexOf(v)} {requestId} />
      {/each}
    </div>
  {/each}
  {#if !loading && !done && videos.length}<div use:nearEnd={load}></div>{/if}
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>
