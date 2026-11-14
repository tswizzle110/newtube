<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from './VideoCard.svelte'
  import { prefs, setAutoplay } from '../lib/prefs.svelte.js'

  // "Up next": videos related to the one being watched. Asked for a moment AFTER playback has had its chance to
  // start, so it never competes with the video for the first requests.
  let { videoId, onloaded = null } = $props()
  const requestId = uid()
  let videos = $state([])
  let loading = $state(true)
  let status = $state('ok')    // ok | empty | paused (bot check) | failed
  let source = $state('youtube') // 'local': YouTube's list is not available, these come from this server's own library

  async function load(force = false) {
    loading = true
    try {
      const r = await api.get(`/api/related/${videoId}?limit=20${force ? '&force=1' : ''}`)
      videos = r.videos
      onloaded?.(r.videos)
      status = r.status
      source = r.source ?? 'youtube'
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
  <div class="head">
    <h2>Up next</h2>
    <label class="auto" title="When a video ends with nothing queued and no playlist, go on to the first unwatched video in this list">
      <input type="checkbox" checked={prefs.autoplay} onchange={(e) => setAutoplay(e.target.checked)} /> Autoplay
    </label>
  </div>
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
  {#if source === 'local' && videos.length}
    <p class="muted small">
      {status === 'paused' ? "YouTube is asking this server for a bot check, so these come from your own library:"
        : "YouTube's suggestions aren't available for this one, so these come from your own library:"}
      the channel's other videos and similar titles.
      <button class="retry" onclick={() => load(true)}>Try YouTube again</button>
    </p>
  {/if}
  <div class="list">
    {#each videos as v, i (v.id)}
      <VideoCard video={v} surface="related" position={i} {requestId} compact />
    {/each}
  </div>
</section>

<style>
  .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  h2 { font-size: 15px; margin: 0; }
  .auto { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-dim); cursor: pointer; }
  .list { display: flex; flex-direction: column; gap: 12px; }
  .small { font-size: 13px; }
  .retry { font-size: 13px; padding: 4px 10px; }
</style>
