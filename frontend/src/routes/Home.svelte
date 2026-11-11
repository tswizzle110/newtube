<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid, trackEvent } from '../lib/tracker.js'
  import { nearEnd } from '../lib/scroll.js'
  import VideoCard from '../components/VideoCard.svelte'

  const PAGE = 48                 // cards shown at first; more appear as you scroll (the whole ranked list is already loaded)
  let videos = $state([])
  let shown = $state(PAGE)
  let loading = $state(true)
  let error = $state('')
  let requestId = $state(uid())   // groups the cards of one ranking; a refresh starts a new one

  async function load(refresh = false) {
    loading = true; error = ''
    try {
      if (refresh) { requestId = uid(); trackEvent('feed_refresh', { payload: { surface: 'for_you' } }) }
      videos = await api.get('/api/recommendations?limit=240')
      shown = PAGE
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => { document.title = 'newtube'; load() })

  // "Not interested" / "Never this channel": remember it (locally) and drop the card straight away.
  function dismiss(v, type) {
    trackEvent(type, { video_id: v.id, channel_id: v.channel_id || undefined })
    videos = type === 'dont_recommend_channel' && v.channel_id
      ? videos.filter((x) => x.channel_id !== v.channel_id)
      : videos.filter((x) => x.id !== v.id)
  }
</script>

<div class="page">
  <div class="page-head">
    <h1>Home</h1>
    <span class="spacer"></span>
    <button class="btn" onclick={() => load(true)} disabled={loading}>Refresh</button>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !videos.length && !error}
    <p class="empty">Nothing to recommend yet. <a href="/channels" class="link">Add some channels</a> and let their latest videos arrive.</p>
  {/if}
  <div class="grid">
    {#each videos.slice(0, shown) as v, i (v.id)}
      <VideoCard video={v} surface="for_you" position={i} {requestId} why={v.why} ondismiss={(type) => dismiss(v, type)} />
    {/each}
  </div>
  {#if videos.length > shown}<div use:nearEnd={() => (shown += PAGE)}></div>{/if}
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>
