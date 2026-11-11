<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import { nearEnd } from '../lib/scroll.js'
  import VideoCard from '../components/VideoCard.svelte'

  const PAGE = 48
  let videos = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')
  const requestId = uid() // groups every card shown during this visit to the feed

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
  onMount(() => { document.title = 'Subscriptions · newtube'; load() })
</script>

<div class="page">
  <div class="page-head">
    <h1>Subscriptions</h1>
    <span class="spacer"></span>
    <a class="btn" href="/channels">Manage channels</a>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !videos.length && !error}
    <p class="empty">Nothing here yet. <a href="/channels" class="link">Add some channels</a> to build your feed.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={v} surface="subs" position={i} {requestId} />
    {/each}
  </div>
  {#if !loading && !done && videos.length}<div use:nearEnd={load}></div>{/if}
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>
