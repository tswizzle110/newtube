<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { loadSubs } from '../lib/nav.svelte.js'

  let channels = $state([])
  let ref = $state('')
  let busy = $state(false)
  let error = $state('')
  let note = $state('')
  let fetching = $state(false)
  let pictureNote = $state('')
  const missing = $derived(channels.filter((c) => !c.has_avatar).length)       // subscriptions that show a letter instead of a picture
  let broken = $state({})          // channels whose picture would not load: show their letter instead

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

  // Find the missing pictures now, a small batch at a time (the background job does the same, slowly).
  async function fetchPictures() {
    fetching = true; pictureNote = ''; error = ''
    try {
      const r = await api.post('/api/subscriptions/pictures?limit=15')
      pictureNote = r.stopped === 'bot'
        ? `YouTube asked for a bot check, so newtube stopped. Found ${r.found}. Try again in a while.`
        : `Found ${r.found} of ${r.tried}.${r.left ? ` ${r.left} more to go: press the button again.` : ''}`
      await load()
    } catch (err) { error = err.message } finally { fetching = false }
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

  {#if missing}
    <p class="muted">
      {missing} channel{missing === 1 ? ' has' : 's have'} no picture yet (newtube is filling them in slowly in the background).
      <button class="btn small" onclick={fetchPictures} disabled={fetching}>{fetching ? 'Fetching…' : 'Fetch them now'}</button>
      {#if pictureNote}<span>{pictureNote}</span>{/if}
    </p>
  {:else if pictureNote}<p class="muted">{pictureNote}</p>{/if}

  <ul>
    {#each channels as c (c.id)}
      <li>
        <a class="who" href={`/channel/${c.id}`}>
          {#if c.has_avatar && !broken[c.id]}
            <img src={`/api/avatar/${c.id}`} alt="" onerror={() => (broken[c.id] = true)} />
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
