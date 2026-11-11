<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import { loadLists } from '../lib/nav.svelte.js'
  import { navigate } from '../lib/router.svelte.js'
  import { nearEnd } from '../lib/scroll.js'
  import VideoCard from '../components/VideoCard.svelte'
  import Menu from '../components/Menu.svelte'

  const ref = router.pathname.split('/')[2]
  const PAGE = 60
  const requestId = uid()
  let info = $state(null)
  let videos = $state([])
  let loading = $state(true)
  let done = $state(false)
  let error = $state('')

  async function load() {
    if (loading && videos.length) return
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
    <div class="page-head">
      {#if editing}
        <form class="rename" onsubmit={saveName}>
          <input class="input" bind:value={nameDraft} aria-label="Playlist name" maxlength="100" />
          <button class="btn primary">Save</button>
          <button type="button" class="btn" onclick={() => (editing = false)}>Cancel</button>
        </form>
      {:else}
        <h1>{info.name}</h1>
        <span class="meta">{info.count.toLocaleString()} video{info.count === 1 ? '' : 's'}</span>
      {/if}
      <span class="spacer"></span>
      {#if videos.length && !editing}<a class="btn primary" href={`/watch?v=${videos[0].id}&src=playlist&mylist=${ref}`}>▶ Play all</a>{/if}
      {#if own && !editing}
        <Menu label="Playlist actions" items={[{ label: 'Rename', onclick: () => { nameDraft = info.name; editing = true } },
                                                { label: 'Delete playlist', danger: true, onclick: deleteList }]} />
      {/if}
    </div>
  {/if}
  {#if !loading && !videos.length && !error}
    <p class="empty">Nothing here yet.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={{ ...v, title: v.title || v.id }} surface="playlist" position={i} {requestId}
                 extra={`&mylist=${ref}`} onremove={() => remove(v)}
                 onup={own && i > 0 ? () => move(i, -1) : null} ondown={own && i < videos.length - 1 ? () => move(i, 1) : null} />
    {/each}
  </div>
  {#if loading}<p class="muted">Loading…</p>{/if}
  {#if !loading && !done && videos.length}<div use:nearEnd={load}></div>{/if}
</div>

<style>
  .rename { display: flex; gap: 8px; width: min(100%, 480px); }
  .rename .input { flex: 1; }
</style>
