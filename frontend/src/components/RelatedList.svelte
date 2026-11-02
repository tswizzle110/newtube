<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from './VideoCard.svelte'

  // "Up next": videos related to the one being watched. Asked for a moment AFTER playback has had its chance to
  // start, so it never competes with the video for the first requests.
  let { videoId } = $props()
  const requestId = uid()
  let videos = $state([])
  let loading = $state(true)

  onMount(() => {
    const timer = setTimeout(async () => {
      try {
        videos = await api.get(`/api/related/${videoId}?limit=20`)
      } catch { /* the page works without suggestions */ } finally {
        loading = false
      }
    }, 1200)
    return () => clearTimeout(timer)
  })
</script>

<section aria-label="Up next">
  <h2>Up next</h2>
  {#if loading}
    <p class="muted small">Looking for related videos…</p>
  {:else if !videos.length}
    <p class="muted small">No related videos found.</p>
  {/if}
  <div class="list">
    {#each videos as v, i (v.id)}
      <VideoCard video={v} surface="related" position={i} {requestId} compact />
    {/each}
  </div>
</section>

<style>
  h2 { font-size: 15px; margin: 0 0 10px; }
  .list { display: flex; flex-direction: column; gap: 12px; }
  .small { font-size: 13px; }
</style>
