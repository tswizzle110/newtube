<script>
  import { api } from '../lib/api.js'
  import { loadLists } from '../lib/nav.svelte.js'

  // The Like and Watch later toggles under a video. Optimistic: the button flips at once and flips back if the
  // server says no.
  let { videoId, liked = false, watchLater = false } = $props()
  let state = $state({ liked, watch_later: watchLater })
  let busy = $state(false)
  let error = $state('')

  async function toggle(list) {
    if (busy) return
    busy = true; error = ''
    const was = state[list]
    state[list] = !was
    try {
      const r = was ? await api.del(`/api/mylists/${list}/items/${videoId}`) : await api.post(`/api/mylists/${list}/items`, { video_id: videoId })
      state.liked = r.liked; state.watch_later = r.watch_later
      loadLists()
    } catch (e) {
      state[list] = was
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="actions">
  <button class="btn" class:on={state.liked} aria-pressed={state.liked} onclick={() => toggle('liked')}
          title={state.liked ? 'Remove from Liked videos' : 'Add to Liked videos'}>
    {state.liked ? '♥ Liked' : '♡ Like'}
  </button>
  <button class="btn" class:on={state.watch_later} aria-pressed={state.watch_later} onclick={() => toggle('watch_later')}
          title={state.watch_later ? 'Remove from Watch later' : 'Save to Watch later'}>
    {state.watch_later ? '✓ In Watch later' : '+ Watch later'}
  </button>
  {#if error}<span class="error small">{error}</span>{/if}
</div>

<style>
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .btn.on { color: var(--accent); border-color: var(--accent); }
  .small { font-size: 13px; }
</style>
