<script>
  import { api } from '../lib/api.js'
  import { loadSubs } from '../lib/nav.svelte.js'

  // Subscribe / unsubscribe right where you are (the video page), instead of going to Manage channels.
  let { channelId, subscribed = false } = $props()
  let on = $state(subscribed)
  let busy = $state(false)
  let error = $state('')

  async function toggle() {
    busy = true; error = ''
    try {
      if (on) await api.del(`/api/subscriptions/${channelId}`)
      else await api.post('/api/subscriptions', { ref: channelId })
      on = !on
      loadSubs()                                    // keep the sidebar's channel list in step
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<button class="btn small" class:primary={!on} disabled={busy} onclick={toggle} aria-pressed={on} title={error || ''}>
  {on ? 'Subscribed ✓' : 'Subscribe'}
</button>
