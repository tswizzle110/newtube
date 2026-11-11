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
    <div class="page-head">
      <h1>{page.playlist.title}</h1>
      <span class="meta">{[page.playlist.channel_title, `${page.videos.length} videos`].filter(Boolean).join(' · ')}</span>
      <span class="spacer"></span>
      {#if page.videos.length}
        <a class="btn primary" href={`/watch?v=${page.videos[0].id}&src=playlist&list=${id}`}>▶ Play all</a>
      {/if}
    </div>
    <div class="grid">
      {#each page.videos as v, i (v.id)}
        <VideoCard video={v} surface="playlist" position={i} {requestId} extra={`&list=${id}`} />
      {/each}
    </div>
  {/if}
</div>
