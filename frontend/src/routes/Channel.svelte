<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { subscribers, img } from '../lib/format.js'
  import { uid } from '../lib/tracker.js'
  import { loadSubs } from '../lib/nav.svelte.js'
  import VideoCard from '../components/VideoCard.svelte'

  const id = router.pathname.split('/')[2]
  const SORTS = [['newest', 'Newest'], ['popular', 'Popular'], ['oldest', 'Oldest']]
  const sort = SORTS.some(([k]) => k === router.query.get('sort')) ? router.query.get('sort') : 'newest'
  const requestId = uid()
  let page = $state(null)
  let subscribed = $state(false)
  let error = $state('')
  let busy = $state(false)
  let limit = $state(30)
  let loading = $state(false)

  async function load() {
    loading = true; error = ''
    try {
      page = await api.get(`/api/channel/${id}?sort=${sort}&limit=${limit}`)
      subscribed = page.subscribed
      document.title = `${page.channel.title} · newtube`
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(load)

  // Newest: YouTube's list can't be paged, so "load more" asks for a longer one (capped). Popular/Oldest are
  // sorted from a list we already hold, so they can go much further at no extra cost.
  const maxLimit = sort === 'newest' ? 120 : 500
  function more() { limit = Math.min(limit + 30, maxLimit); load() }

  async function toggleSub() {
    busy = true
    try {
      if (subscribed) { await api.del(`/api/subscriptions/${id}`); subscribed = false }
      else { await api.post('/api/subscriptions', { ref: id }); subscribed = true }
      loadSubs()                                     // keep the sidebar list in step
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="page">
  {#if error}<p class="error">{error}</p>{/if}
  {#if !page && !error}<p class="muted">Loading…</p>{/if}
  {#if page}
    <header class="head">
      {#if page.channel.avatar_url}
        <img src={img(page.channel.avatar_url)} alt="" />
      {:else}
        <span class="ph">{page.channel.title[0]}</span>
      {/if}
      <div class="meta">
        <h1>{page.channel.title}</h1>
        <div class="muted">{[page.channel.handle, subscribers(page.channel.subscriber_count)].filter(Boolean).join(' · ')}</div>
        {#if page.channel.description}<p class="muted desc">{page.channel.description}</p>{/if}
      </div>
      <button class="btn" class:primary={!subscribed} disabled={busy} onclick={toggleSub}>
        {subscribed ? 'Subscribed ✓' : 'Subscribe'}
      </button>
    </header>
    <div class="sorts" role="group" aria-label="Sort videos">
      {#each SORTS as [k, label]}
        <a href={`/channel/${id}${k === 'newest' ? '' : '?sort=' + k}`} class:on={k === sort}>{label}</a>
      {/each}
    </div>
    <div class="grid">
      {#each page.videos as v, i (v.id)}
        <VideoCard video={v} surface="channel" position={i} {requestId} />
      {/each}
    </div>
    {#if page.unavailable}
      <p class="muted">{page.unavailable} <a class="link" href={`/channel/${id}`}>Show newest instead</a></p>
    {:else if !page.videos.length && !loading}
      <p class="muted">No videos found.</p>
    {/if}
    {#if loading}<p class="muted">Loading…</p>{/if}
    {#if !loading && page.has_more && limit < maxLimit}
      <p><button class="btn" onclick={more}>Load more videos</button></p>
    {/if}
    {#if page.notice}<p class="muted small">{page.notice}</p>{/if}
  {/if}
</div>

<style>
  .head { display: flex; gap: 24px; align-items: center; margin-bottom: 28px; }
  .head img, .ph { width: 112px; height: 112px; border-radius: 50%; object-fit: cover; flex: none; }
  .ph { background: var(--bg-hover); display: grid; place-items: center; font-size: 40px; font-weight: 700; }
  .meta { flex: 1; min-width: 0; }
  h1 { margin: 0 0 4px; font-size: 26px; }
  .sorts { display: flex; gap: 6px; margin-bottom: 18px; }
  .sorts a { padding: 6px 14px; border-radius: 999px; background: var(--bg-elev); color: var(--text-dim); }
  .sorts a:hover { background: var(--bg-hover); color: var(--text); }
  .sorts a.on { background: var(--text); color: var(--bg); }
  .small { font-size: 13px; }
  .link { color: var(--accent); }
  .desc { margin: 8px 0 0; max-width: 70ch; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
