<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { COLORS } from '../lib/sponsorblock.js'

  const ACTIONS = [['auto', 'Skip automatically'], ['button', 'Show skip button'], ['mark', 'Only mark on the bar'], ['ignore', 'Ignore']]
  let s = $state(null)
  let status = $state('')
  let error = $state('')
  let timer

  onMount(async () => {
    document.title = 'Settings · newtube'
    try { s = await api.get('/api/settings/sponsorblock') } catch (e) { error = e.message }
  })

  async function save() {
    error = ''
    try {
      const saved = await api.put('/api/settings/sponsorblock', {
        categories: s.categories, min_length: Number(s.min_length), show_toast: s.show_toast,
        contribute: s.contribute, channels: s.channels,
      })
      s = { ...s, ...saved }
      status = 'Saved'
      clearTimeout(timer); timer = setTimeout(() => (status = ''), 1500)
    } catch (e) {
      error = e.message
    }
  }
  function removeChannel(id) { delete s.channels[id]; save() }
</script>

<div class="page narrow">
  <h1>Settings</h1>
  {#if error}<p class="error">{error}</p>{/if}
  {#if s}
    <h2>SponsorBlock <span class="status muted">{status}</span></h2>
    <p class="muted">
      Community-submitted segments (sponsors, intros, ...). Looking them up is private: the server asks
      SponsorBlock about a 4-character code that matches many videos, and picks yours out locally.
    </p>
    <table class="cats">
      <tbody>
        {#each s.category_order as c}
          <tr>
            <td><i style:background={COLORS[c]}></i>{s.category_labels[c]}</td>
            <td>
              <select bind:value={s.categories[c]} onchange={save}>
                {#each ACTIONS as [k, label]}<option value={k}>{label}</option>{/each}
              </select>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    <div class="row">
      <label>Ignore segments shorter than
        <input class="input num" type="number" min="0" max="60" step="0.5" bind:value={s.min_length} onchange={save} /> seconds
      </label>
    </div>
    <div class="row">
      <label><input type="checkbox" bind:checked={s.show_toast} onchange={save} /> Show a “Skipped …” message with Undo</label>
    </div>

    <h3>Contributing</h3>
    <div class="row">
      <label><input type="checkbox" bind:checked={s.contribute} onchange={save} /> Allow submitting segments and voting</label>
    </div>
    <p class="warn">
      Submitting a segment or voting <b>tells SponsorBlock's server which video you mean</b>
      (reading does not). It uses a random ID stored on this server, not tied to any account. Off by default.
    </p>

    {#if Object.keys(s.channels).length}
      <h3>Channel overrides</h3>
      <ul class="over">
        {#each Object.entries(s.channels) as [id, per]}
          <li>
            <a href={`/channel/${id}`}>{id}</a>
            <span class="muted">{Object.entries(per).map(([c, a]) => `${s.category_labels[c]}: ${a}`).join(', ')}</span>
            <button class="btn" onclick={() => removeChannel(id)}>Remove</button>
          </li>
        {/each}
      </ul>
    {/if}
  {:else if !error}
    <p class="muted">Loading…</p>
  {/if}
</div>

<style>
  .narrow { max-width: 760px; }
  h2 { margin-top: 8px; } h3 { margin: 28px 0 8px; font-size: 15px; }
  .status { font-size: 13px; font-weight: 400; margin-left: 10px; }
  .cats { border-spacing: 0 6px; } .cats td { padding-right: 24px; }
  i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }
  select { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; color: var(--text); }
  .row { margin: 12px 0; } .num { width: 70px; padding: 4px 8px; }
  .warn { background: var(--bg-elev); border-left: 4px solid var(--danger); padding: 10px 14px; border-radius: 6px; }
  .over { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .over li { display: flex; gap: 14px; align-items: center; } .over .muted { flex: 1; font-size: 13px; }
</style>
