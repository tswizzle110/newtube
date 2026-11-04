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
  let status = $state('ok')    // ok | empty | paused (bot check) | failed

  async function load(force = false) {
    loading = true
    try {
      const r = await api.get(`/api/related/${videoId}?limit=20${force ? '&force=1' : ''}`)
      videos = r.videos
      status = r.status
    } catch {
      status = 'failed'        // the page works without suggestions
    } finally {
      loading = false
    }
  }

  onMount(() => {
    const timer = setTimeout(() => load(), 1200)
    return () => clearTimeout(timer)
  })
</script>

<section aria-label="Up next">
  <h2>Up next</h2>
  {#if loading}
    <p class="muted small">Looking for related videos…</p>
  {:else if !videos.length}
    {#if status === 'paused'}
      <p class="muted small">YouTube is asking this server for a bot check, so suggestions are paused until it
        clears. Nothing is lost; try again later.</p>
    {:else if status === 'empty'}
      <p class="muted small">YouTube returned no related videos for this one.</p>
    {:else}
      <p class="muted small">Couldn't load related videos right now.</p>
    {/if}
    {#if status !== 'empty'}<button class="retry" onclick={() => load(true)}>Try again</button>{/if}
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
  .retry { font-size: 13px; padding: 4px 10px; }
</style>
