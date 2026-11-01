<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'

  const id = router.query.get('list')
  const requestId = uid()
  let page = $state(null)
  let error = $state('')

  onMount(async () => {
    try {
      page = await api.get(`/api/playlist/${id}`)
      document.title = `${page.playlist.title} · newtube`
    } catch (e) {
      error = e.message
    }
  })
</script>

<div class="page">
  {#if error}<p class="error">{error}</p>{/if}
  {#if !page && !error}<p class="muted">Loading…</p>{/if}
  {#if page}
    <header>
      <h1>{page.playlist.title}</h1>
      <div class="muted">
        {[page.playlist.channel_title, `${page.videos.length} videos`].filter(Boolean).join(' · ')}
      </div>
      {#if page.videos.length}
        <p><a class="btn primary" href={`/watch?v=${page.videos[0].id}&src=playlist&list=${id}`}>▶ Play all</a></p>
      {/if}
    </header>
    <div class="grid">
      {#each page.videos as v, i (v.id)}
        <VideoCard video={v} surface="playlist" position={i} {requestId} extra={`&list=${id}`} />
      {/each}
    </div>
  {/if}
</div>

<style>
  header { margin-bottom: 24px; }
  h1 { margin: 0 0 6px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
