<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid, trackEvent } from '../lib/tracker.js'
  import VideoCard from '../components/VideoCard.svelte'
  import HomeTabs from '../components/HomeTabs.svelte'

  const SHELF = 6
  const PAGE = 48                 // cards shown at first; "Show more" adds another page (the whole ranked list is already loaded)
  let shown = $state(PAGE)
  let videos = $state([])
  let fresh = $state([])        // newest unwatched uploads from your subscriptions (the shelf on top)
  let loading = $state(true)
  let error = $state('')
  let requestId = $state(uid()) // groups the cards of one ranking; a refresh starts a new one
  const shelfRequest = uid()

  async function load(refresh = false) {
    loading = true; error = ''
    try {
      if (refresh) { requestId = uid(); trackEvent('feed_refresh', { payload: { surface: 'for_you' } }) }
      const [recs, feed] = await Promise.all([
        api.get('/api/recommendations?limit=240'),
        api.get('/api/feed?limit=24').catch(() => []),
      ])
      fresh = feed.filter((v) => !v.seen && !v.completed).slice(0, SHELF)
      videos = recs
      shown = PAGE
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => { document.title = 'newtube'; load() })

  // The ranked list never repeats what the shelf already shows.
  const ranked = $derived(videos.filter((v) => !fresh.some((f) => f.id === v.id)))

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
  {#if error}<p class="error">{error}</p>{/if}

  {#if fresh.length}
    <section aria-label="New from your subscriptions">
      <div class="head"><h2>New from your subscriptions</h2><a class="muted" href="/subscriptions">See all</a></div>
      <div class="shelf">
        {#each fresh as v, i (v.id)}
          <VideoCard video={v} surface="subs" position={i} requestId={shelfRequest} />
        {/each}
      </div>
    </section>
  {/if}

  <div class="head">
    <h2>Recommended for you</h2>
    <button class="btn" onclick={() => load(true)} disabled={loading}>Refresh</button>
  </div>
  <p class="muted small">
    Picked from your subscriptions, Watch later, channels you watch, and videos about what you like, using your own history.
    Nothing leaves this server. <strong>Why this?</strong> under a video shows the reasons.
  </p>
  {#if !loading && !ranked.length && !fresh.length && !error}
    <p class="muted">Nothing to recommend yet. Subscribe to some channels, or
      <a href="/channels" class="link">add channels</a> and let their latest videos arrive.</p>
  {/if}
  <div class="grid">
    {#each ranked.slice(0, shown) as v, i (v.id)}
      <VideoCard video={v} surface="for_you" position={i} {requestId} why={v.why}
                 ondismiss={(type) => dismiss(v, type)} />
    {/each}
  </div>
  {#if ranked.length > shown}
    <p class="more"><button class="btn" onclick={() => (shown += PAGE)}>Show more ({ranked.length - shown} left)</button></p>
  {/if}
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 28px 18px; }
  .head { display: flex; align-items: baseline; gap: 14px; margin: 6px 0 12px; }
  .head h2 { font-size: 17px; margin: 0; }
  .head :global(.btn), .head a { margin-left: auto; }
  .shelf { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(240px, 280px); gap: 16px; overflow-x: auto;
    padding-bottom: 12px; margin-bottom: 18px; }
  .small { font-size: 13px; margin: 0 0 16px; }
  .more { text-align: center; margin: 24px 0; }
  .link { color: var(--accent); }
</style>
