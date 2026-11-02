<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid, trackEvent } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'
  import HomeTabs from '../components/HomeTabs.svelte'

  let videos = $state([])
  let loading = $state(true)
  let error = $state('')
  let requestId = $state(uid()) // groups the cards of one ranking; a refresh starts a new one

  async function load(refresh = false) {
    loading = true; error = ''
    try {
      if (refresh) { requestId = uid(); trackEvent('feed_refresh', { payload: { surface: 'for_you' } }) }
      videos = await api.get('/api/recommendations?limit=60')
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => load())

  // "Not interested" / "Never this channel": remember it (locally) and drop the card straight away.
  function dismiss(v, type) {
    trackEvent(type, { video_id: v.id, channel_id: v.channel_id || undefined })
    videos = type === 'dont_recommend_channel' && v.channel_id
      ? videos.filter((x) => x.channel_id !== v.channel_id)
      : videos.filter((x) => x.id !== v.id)
  }
</script>

<div class="page">
  <HomeTabs />
  <p class="muted lead">
    Picked from your subscriptions and Watch later using your own history. Nothing leaves this server.
    <button class="btn" onclick={() => load(true)} disabled={loading}>Refresh</button>
  </p>
  {#if error}<p class="error">{error}</p>{/if}
  {#if !loading && !videos.length && !error}
    <p class="muted">Nothing to recommend yet. Subscribe to some channels, or
      <a href="/channels" class="link">add channels</a> and let their latest videos arrive.</p>
  {/if}
  <div class="grid">
    {#each videos as v, i (v.id)}
      <VideoCard video={v} surface="for_you" position={i} {requestId} why={v.why}
                 ondismiss={(type) => dismiss(v, type)} />
    {/each}
  </div>
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
  .lead { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .link { color: var(--accent); }
</style>
