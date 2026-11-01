<script>
  import { subscribers, img } from '../lib/format.js'
  import { trackEvent } from '../lib/tracker.js'
  import { navigate } from '../lib/router.svelte.js'

  let { channel, searchQuery = null, position = 0 } = $props()

  function open(e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
    e.preventDefault()
    trackEvent('search_click', {
      channel_id: channel.id, payload: { q: searchQuery, kind: 'channel', position },
    })
    navigate(`/channel/${channel.id}`)
  }
</script>

<a class="row" href={`/channel/${channel.id}`} onclick={open}>
  {#if channel.avatar_url}
    <img src={img(channel.avatar_url)} alt="" loading="lazy" />
  {:else}
    <span class="ph">{channel.title[0]}</span>
  {/if}
  <div class="info">
    <div class="name">{channel.title}</div>
    <div class="muted small">{[channel.handle, subscribers(channel.subscriber_count)].filter(Boolean).join(' · ')}</div>
    {#if channel.description}<div class="muted small desc">{channel.description}</div>{/if}
  </div>
</a>

<style>
  .row { display: flex; gap: 20px; align-items: center; padding: 14px 12px; border-radius: var(--radius); }
  .row:hover { background: var(--bg-elev); }
  img, .ph { width: 88px; height: 88px; border-radius: 50%; flex: none; object-fit: cover; }
  .ph { background: var(--bg-hover); display: grid; place-items: center; font-size: 32px; font-weight: 700; }
  .name { font-size: 17px; font-weight: 600; }
  .small { font-size: 13px; }
  .desc { margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
