<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { loadSubs } from '../lib/nav.svelte.js'

  let channels = $state([])
  let ref = $state('')
  let busy = $state(false)
  let error = $state('')
  let note = $state('')

  const load = async () => { channels = await api.get('/api/subscriptions'); loadSubs() }   // and the sidebar's copy
  onMount(load)

  async function add(e) {
    e.preventDefault()
    busy = true; error = ''; note = ''
    try {
      const r = await api.post('/api/subscriptions', { ref })
      if (r.poll_error) error = `Subscribed, but fetching videos failed: ${r.poll_error}`
      note = r.created ? `Subscribed to ${r.title} (${r.new_videos} videos found).` : `Already subscribed to ${r.title}.`
      ref = ''
      await load()
    } catch (err) { error = err.message } finally { busy = false }
  }

  // Unsubscribing happens at once (no "are you sure?" click); the note below it offers Undo instead.
  let undo = $state(null)
  async function remove(c) {
    await api.del(`/api/subscriptions/${c.id}`)
    undo = c
    await load()
  }
  async function restore() {
    const c = undo
    undo = null
    try { await api.post('/api/subscriptions', { ref: c.id }); await load() } catch (err) { error = err.message }
  }
</script>

<div class="page narrow">
  <div class="page-head">
    <h1>Manage channels</h1>
    <span class="spacer"></span>
    <a class="btn" href="/subscriptions">← Subscriptions</a>
  </div>
  <form class="add" onsubmit={add}>
    <input class="input" bind:value={ref} placeholder="@handle, channel URL or UC… id" />
    <button class="btn primary" disabled={busy || !ref.trim()}>{busy ? 'Adding…' : 'Subscribe'}</button>
  </form>
  {#if error}<p class="error">{error}</p>{/if}
  {#if note}<p class="muted">{note}</p>{/if}
  {#if undo}<p class="muted">Unsubscribed from {undo.title}. <button class="link-btn" onclick={restore}>Undo</button></p>{/if}

  <ul>
    {#each channels as c (c.id)}
      <li>
        <a class="who" href={`/channel/${c.id}`}>
          {#if c.has_avatar}
            <img src={`/api/avatar/${c.id}`} alt="" />
          {:else}
            <span class="ph">{c.title[0]}</span>
          {/if}
          <span class="name">{c.title}<small class="muted">{c.handle ?? ''}</small></span>
        </a>
        <button class="btn danger" onclick={() => remove(c)}>Unsubscribe</button>
      </li>
    {/each}
  </ul>
  {#if !channels.length}<p class="empty">You aren't subscribed to anything yet.</p>{/if}
</div>

<style>
  .add { display: flex; gap: 8px; margin-bottom: 12px; }
  .add .input { flex: 1; }
  ul { list-style: none; padding: 0; margin: 16px 0; display: flex; flex-direction: column; gap: 4px; }
  li { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px;
    border-radius: var(--radius); }
  li:hover { background: var(--bg-elev); }
  .who { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .who .name { display: flex; flex-direction: column; }
  img, .ph { width: 40px; height: 40px; border-radius: 50%; flex: none; }
  .ph { background: var(--bg-hover); display: grid; place-items: center; font-weight: 700; }
</style>
