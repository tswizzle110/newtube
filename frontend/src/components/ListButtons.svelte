<script>
  import { api } from '../lib/api.js'
  import { mylists, loadLists } from '../lib/nav.svelte.js'

  // Like / Watch later toggles and the "Save" menu (your own playlists) under a video. Optimistic: a control
  // flips at once and flips back if the server says no.
  let { videoId, liked = false, watchLater = false, playlists = [] } = $props()
  // (the initial values are all that is wanted here: this component is created fresh for each video)
  let state = $state({ liked, watch_later: watchLater, playlists: [...playlists] })
  let busy = $state(false)
  let error = $state('')
  let open = $state(false)
  let newName = $state('')
  let root

  const mine = $derived(mylists.list.filter((l) => l.kind === 'user'))

  function apply(r) { state.liked = r.liked; state.watch_later = r.watch_later; state.playlists = r.playlists }

  async function toggle(list) {
    if (busy) return
    busy = true; error = ''
    const was = state[list]
    state[list] = !was
    try {
      apply(was ? await api.del(`/api/mylists/${list}/items/${videoId}`) : await api.post(`/api/mylists/${list}/items`, { video_id: videoId }))
      loadLists()
    } catch (e) {
      state[list] = was
      error = e.message
    } finally {
      busy = false
    }
  }

  async function togglePlaylist(p) {
    if (busy) return
    busy = true; error = ''
    const was = state.playlists.includes(p.id)
    try {
      apply(was ? await api.del(`/api/mylists/${p.id}/items/${videoId}`) : await api.post(`/api/mylists/${p.id}/items`, { video_id: videoId }))
      loadLists()
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  async function createAndSave(e) {
    e.preventDefault()
    const name = newName.trim()
    if (!name || busy) return
    busy = true; error = ''
    try {
      const p = await api.post('/api/mylists', { name })
      apply(await api.post(`/api/mylists/${p.id}/items`, { video_id: videoId }))
      newName = ''
      await loadLists()
    } catch (e2) {
      error = e2.message
    } finally {
      busy = false
    }
  }

  function outside(e) { if (open && root && !root.contains(e.target)) open = false }
</script>

<svelte:window onclick={outside} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<div class="actions" bind:this={root}>
  <button class="btn" class:on={state.liked} aria-pressed={state.liked} onclick={() => toggle('liked')}
          title={state.liked ? 'Remove from Liked videos' : 'Add to Liked videos'}>
    {state.liked ? '♥ Liked' : '♡ Like'}
  </button>
  <button class="btn" class:on={state.watch_later} aria-pressed={state.watch_later} onclick={() => toggle('watch_later')}
          title={state.watch_later ? 'Remove from Watch later' : 'Save to Watch later'}>
    {state.watch_later ? '✓ In Watch later' : '+ Watch later'}
  </button>
  <div class="menu-wrap">
    <button class="btn" class:on={state.playlists.length > 0} aria-expanded={open} aria-haspopup="true"
            onclick={() => (open = !open)} title="Save to a playlist">
      {state.playlists.length ? `☰ In ${state.playlists.length} playlist${state.playlists.length === 1 ? '' : 's'}` : '☰ Save'}
    </button>
    {#if open}
      <div class="menu" role="group" aria-label="Save to playlist">
        {#each mine as p (p.id)}
          <label><input type="checkbox" checked={state.playlists.includes(p.id)} onchange={() => togglePlaylist(p)} /> <span>{p.name}</span></label>
        {:else}
          <p class="muted small">No playlists yet.</p>
        {/each}
        <form onsubmit={createAndSave}>
          <input class="input" bind:value={newName} placeholder="New playlist name" aria-label="New playlist name" maxlength="100" />
          <button class="btn" disabled={!newName.trim()}>Create &amp; save</button>
        </form>
      </div>
    {/if}
  </div>
  {#if error}<span class="error small">{error}</span>{/if}
</div>

<style>
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; position: relative; }
  .btn.on { color: var(--accent); border-color: var(--accent); }
  .small { font-size: 13px; }
  .menu-wrap { position: relative; }
  .menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; min-width: 260px; max-height: 340px; overflow: auto;
    background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35); display: flex; flex-direction: column; gap: 6px; }
  .menu label { display: flex; gap: 8px; align-items: center; padding: 3px 2px; cursor: pointer; }
  .menu form { display: flex; gap: 6px; margin-top: 4px; padding-top: 8px; border-top: 1px solid var(--border); }
  .menu form .input { flex: 1; min-width: 0; }
</style>
