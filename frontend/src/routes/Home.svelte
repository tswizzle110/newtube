<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { uid, trackEvent } from '../lib/tracker.js'
  import { nearEnd } from '../lib/scroll.js'
  import VideoCard from '../components/VideoCard.svelte'

  const PAGE = 48                 // cards shown at first; more appear as you scroll (the whole ranked list is already loaded)
  let videos = $state([])
  let resume = $state([])           // videos you started and did not finish: they live HERE, never in the grid below
  let showAllResume = $state(false)
  let topic = $state('')           // the topic chip chosen ('' = all)
  const RESUME_ROW = 8
  const resumeRequest = uid()
  let shown = $state(PAGE)
  let loading = $state(true)
  let error = $state('')
  let requestId = $state(uid())   // groups the cards of one ranking; a refresh starts a new one

  async function load(refresh = false) {
    loading = true; error = ''
    try {
      if (refresh) { requestId = uid(); trackEvent('feed_refresh', { payload: { surface: 'for_you' } }) }
      const [started, recs] = await Promise.all([api.get('/api/continue').catch(() => []), api.get('/api/recommendations?limit=240')])
      resume = started
      videos = recs
      shown = PAGE
      if (topic && !videos.some((v) => v.category === topic)) topic = ''
    } catch (e) {
      error = e.message
    } finally {
      loading = false
    }
  }
  onMount(() => { document.title = 'newtube'; load() })

  // Topic chips: the categories YouTube gave the recommended videos (known once a video's details have been stored).
  const topics = $derived.by(() => {
    const n = new Map()
    for (const v of videos) if (v.category) n.set(v.category, (n.get(v.category) ?? 0) + 1)
    return [...n].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([name]) => name)
  })
  const filtered = $derived(topic ? videos.filter((v) => v.category === topic) : videos)

  // "Mark as watched": finished without watching the rest; it leaves the row.
  async function markWatched(v) {
    try {
      await api.post(`/api/continue/${v.id}/done`)
      resume = resume.filter((x) => x.id !== v.id)
    } catch (e) {
      error = e.message
    }
  }

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
  {#if !loading && !videos.length && !resume.length && !error}
    <p class="empty">Nothing to recommend yet. <a href="/channels" class="link">Add some channels</a> and let their latest videos arrive.</p>
  {/if}
  {#if resume.length}
    <section aria-label="Continue watching">
      <div class="page-head sub">
        <h2>Continue watching</h2>
        {#if resume.length > RESUME_ROW}
          <span class="spacer"></span>
          <button class="link-btn" onclick={() => (showAllResume = !showAllResume)}>{showAllResume ? 'Show fewer' : `Show all ${resume.length}`}</button>
        {/if}
      </div>
      <div class="grid">
        {#each (showAllResume ? resume : resume.slice(0, RESUME_ROW)) as v, i (v.id)}
          <VideoCard video={v} surface="continue" position={i} requestId={resumeRequest}
                     onremove={() => markWatched(v)} removeLabel="Mark as watched" />
        {/each}
      </div>
    </section>
    <h2 class="section">Recommended</h2>
  {/if}
  {#if topics.length > 1}
    <div class="chips topics" role="group" aria-label="Topics">
      <button class="chip" class:active={!topic} onclick={() => { topic = ''; shown = PAGE }}>All</button>
      {#each topics as c}<button class="chip" class:active={topic === c} onclick={() => { topic = c; shown = PAGE }}>{c}</button>{/each}
    </div>
  {/if}
  <div class="grid">
    {#each filtered.slice(0, shown) as v, i (v.id)}
      <VideoCard video={v} surface="for_you" position={i} {requestId} why={v.why} ondismiss={(type) => dismiss(v, type)} />
    {/each}
  </div>
  {#if filtered.length > shown}<div use:nearEnd={() => (shown += PAGE)}></div>{/if}
  {#if loading}<p class="muted">Loading…</p>{/if}
</div>

<style>
  .page-head.sub { margin-bottom: 12px; min-height: 0; }
  section { margin-bottom: 8px; }
  .topics { margin: 0 0 18px; }
</style>
