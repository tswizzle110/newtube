<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { router } from '../lib/router.svelte.js'
  import { subscribers, img } from '../lib/format.js'
  import { uid } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'

  const id = router.pathname.split('/')[2]
  const requestId = uid()
  let page = $state(null)
  let subscribed = $state(false)
  let error = $state('')
  let busy = $state(false)

  onMount(async () => {
    try {
      page = await api.get(`/api/channel/${id}`)
      subscribed = page.subscribed
      document.title = `${page.channel.title} · newtube`
    } catch (e) {
      error = e.message
    }
  })

  async function toggleSub() {
    busy = true
    try {
      if (subscribed) { await api.del(`/api/subscriptions/${id}`); subscribed = false }
      else { await api.post('/api/subscriptions', { ref: id }); subscribed = true }
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
    <h2>Videos</h2>
    <div class="grid">
      {#each page.videos as v, i (v.id)}
        <VideoCard video={v} surface="channel" position={i} {requestId} />
      {/each}
    </div>
    {#if !page.videos.length}<p class="muted">No videos found.</p>{/if}
  {/if}
</div>

<style>
  .head { display: flex; gap: 24px; align-items: center; margin-bottom: 28px; }
  .head img, .ph { width: 112px; height: 112px; border-radius: 50%; object-fit: cover; flex: none; }
  .ph { background: var(--bg-hover); display: grid; place-items: center; font-size: 40px; font-weight: 700; }
  .meta { flex: 1; min-width: 0; }
  h1 { margin: 0 0 4px; font-size: 26px; }
  h2 { font-size: 16px; margin: 0 0 14px; }
  .desc { margin: 8px 0 0; max-width: 70ch; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
</style>
