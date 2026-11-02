<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import { loadLists } from '../lib/nav.svelte.js'
  import { navigate } from '../lib/router.svelte.js'
  import VideoCard from '../components/VideoCard.svelte'

  const ref = router.pathname.split('/')[2]
  const PAGE = 60
  const requestId = uid()
  let info = $state(null)
  let videos = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')

  async function load() {
    loading = true; error = ''
    try {
      const page = await api.get(`/api/mylists/${ref}?limit=${PAGE}&offset=${videos.length}`)
      info = page.list
      document.title = `${page.list.name} · newtube`
      videos.push(...page.videos)
      if (page.videos.length < PAGE) done = true
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(load)

  const own = $derived(info?.kind === 'user')           // a playlist you made: can be renamed, deleted and reordered
  let editing = $state(false)
  let nameDraft = $state('')

  async function saveName(e) {
    e.preventDefault()
    try {
      const r = await api.patch(`/api/mylists/${ref}`, { name: nameDraft })
      info.name = r.name; editing = false; error = ''
      document.title = `${r.name} · newtube`
      loadLists()
    } catch (err) {
      error = err.message
    }
  }

  async function deleteList() {
    if (!confirm(`Delete the playlist "${info.name}"? The videos themselves are not affected.`)) return
    try {
      await api.del(`/api/mylists/${ref}`)
      await loadLists()
      navigate('/')
    } catch (err) {
      error = err.message
    }
  }

  async function move(i, by) {
    const j = i + by
    if (j < 0 || j >= videos.length) return
    const next = [...videos]
    ;[next[i], next[j]] = [next[j], next[i]]
    videos = next
    try {
      await api.put(`/api/mylists/${ref}/order`, { video_ids: next.map((v) => v.id) })
    } catch (err) {
      error = err.message
    }
  }

  async function remove(v) {
    try {
      await api.del(`/api/mylists/${ref}/items/${v.id}`)
      videos = videos.filter((x) => x.id !== v.id)
      if (info) info.count -= 1
      loadLists()
    } catch (e) {
      error = e.message
    }
  }
</script>

<div class="page">
  {#if error}<p class="error">{error}</p>{/if}
  {#if info}
    <header>
      {#if editing}
        <form class="rename" onsubmit={saveName}>
          <input class="input" bind:value={nameDraft} aria-label="Playlist name" maxlength="100" />
          <button class="btn primary">Save</button>
          <button type="button" class="btn" onclick={() => (editing = false)}>Cancel</button>
        </form>
      {:else}
        <h1>{info.name}</h1>
      {/if}
      <div class="muted">{info.count.toLocaleString()} video{info.count === 1 ? '' : 's'}</div>
      <p class="row">
        {#if videos.length}<a class="btn primary" href={`/watch?v=${videos[0].id}&src=playlist&mylist=${ref}`}>▶ Play all</a>{/if}
        {#if own && !editing}
          <button class="btn" onclick={() => { nameDraft = info.name; editing = true }}>Rename</button>
          <button class="btn" onclick={deleteList}>Delete playlist</button>
        {/if}
      </p>
    </header>
  {/if}
  {#if !loading && !videos.length && !error}
    <p class="muted">Nothing here yet.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={{ ...v, title: v.title || v.id }} surface="playlist" position={i} {requestId}
                 extra={`&mylist=${ref}`} onremove={() => remove(v)}
                 onup={own && i > 0 ? () => move(i, -1) : null} ondown={own && i < videos.length - 1 ? () => move(i, 1) : null} />
    {/each}
  </div>
  {#if loading}<p class="muted">Loading…</p>{/if}
  {#if !loading && !done && videos.length}
    <p><button class="btn" onclick={load}>Load more</button></p>
  {/if}
</div>

<style>
  header { margin-bottom: 24px; }
  h1 { margin: 0 0 6px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .rename { display: flex; gap: 8px; max-width: 480px; margin-bottom: 6px; }
  .rename .input { flex: 1; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
