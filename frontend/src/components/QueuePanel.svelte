<script>
  import { uid } from '../lib/tracker.js'
  import { queue, dequeue } from '../lib/queue.svelte.js'
  import VideoCard from './VideoCard.svelte'

  // The queue, beside the video: what plays after this one. Shows the first few; the Queue page has all of it.
  const SHOWN = 5
  const requestId = uid()
</script>

{#if queue.count}
  <section aria-label="Queue" class="queue">
    <div class="head">
      <h2>Queue <span class="muted">· {queue.count}</span></h2>
      <a class="muted small" href="/queue">Open</a>
    </div>
    <div class="list">
      {#each queue.videos.slice(0, SHOWN) as v, i (v.id)}
        <VideoCard video={{ ...v, title: v.title || v.id }} surface="queue" position={i} {requestId} compact quick={false}
                   onremove={() => dequeue(v.id)} removeLabel="Remove from the queue" />
      {/each}
    </div>
    {#if queue.count > SHOWN}<p class="muted small"><a href="/queue">+ {queue.count - SHOWN} more</a></p>{/if}
  </section>
{/if}

<style>
  .queue { margin-bottom: 22px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
  .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; }
  .head h2 { margin: 0; font-size: 15px; }
  .list { display: flex; flex-direction: column; gap: 12px; }
</style>
