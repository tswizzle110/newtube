<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import { loadLists } from '../lib/nav.svelte.js'
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
      <h1>{info.name}</h1>
      <div class="muted">{info.count.toLocaleString()} video{info.count === 1 ? '' : 's'}</div>
      {#if videos.length}
        <p><a class="btn primary" href={`/watch?v=${videos[0].id}&src=playlist&mylist=${ref}`}>▶ Play all</a></p>
      {/if}
    </header>
  {/if}
  {#if !loading && !videos.length && !error}
    <p class="muted">Nothing here yet.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={{ ...v, title: v.title || v.id }} surface="playlist" position={i} {requestId}
                 extra={`&mylist=${ref}`} onremove={() => remove(v)} />
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
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
