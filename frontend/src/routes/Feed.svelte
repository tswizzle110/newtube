<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'
  import HomeTabs from '../components/HomeTabs.svelte'

  const PAGE = 48
  let videos = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')
  const requestId = uid() // groups every card shown during this visit to the feed

  async function load() {
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
  onMount(load)
</script>

<div class="page">
  <HomeTabs />
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !videos.length && !error}
    <p class="muted">Nothing here yet. <a href="/channels" class="link">Add some channels</a> to build your feed.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={v} surface="subs" position={i} {requestId} />
    {/each}
  </div>
  {#if loading}<p class="muted">Loading…</p>{/if}
  {#if !loading && !done && videos.length}
    <p><button class="btn" onclick={load}>Load more</button></p>
  {/if}
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
  .link { color: var(--accent); }
</style>
