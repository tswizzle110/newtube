<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { navigate } from '../lib/router.svelte.js'
  import { uid } from '../lib/tracker.js'
  import { queue, loadQueue, dequeue } from '../lib/queue.svelte.js'
  import VideoCard from '../components/VideoCard.svelte'

  const requestId = uid()
  let error = $state('')
  onMount(() => { document.title = 'Queue · newtube'; loadQueue() })

  async function move(i, by) {
    const j = i + by
    if (j < 0 || j >= queue.videos.length) return
    const next = [...queue.videos]
    ;[next[i], next[j]] = [next[j], next[i]]
    queue.videos = next
    try { await api.put('/api/queue/order', { video_ids: next.map((v) => v.id) }) } catch (e) { error = e.message; loadQueue() }
  }
  async function clear() {
    try { await api.del('/api/queue'); await loadQueue() } catch (e) { error = e.message }
  }
  const play = () => queue.videos.length && navigate(`/watch?v=${queue.videos[0].id}&src=queue`)
</script>

<div class="page">
  <div class="page-head">
    <h1>Queue</h1>
    <span class="meta">{queue.count.toLocaleString()} video{queue.count === 1 ? '' : 's'}</span>
    <span class="spacer"></span>
    {#if queue.count}
      <button class="btn" onclick={clear}>Clear queue</button>
      <button class="btn primary" onclick={play}>▶ Play</button>
    {/if}
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if queue.loaded && !queue.count}
    <p class="empty">Nothing queued. Use <b>Add to queue</b> or <b>Play next</b> in the ⋯ menu of any video. The queue plays one after another, then the video's playlist or Up next carries on.</p>
  {/if}
  <div class="grid">
    {#each queue.videos as v, i (v.id)}
      <VideoCard video={{ ...v, title: v.title || v.id }} surface="queue" position={i} {requestId} quick={false}
                 onremove={() => dequeue(v.id)} removeLabel="Remove from the queue"
                 onup={i > 0 ? () => move(i, -1) : null} ondown={i < queue.videos.length - 1 ? () => move(i, 1) : null} />
    {/each}
  </div>
</div>
