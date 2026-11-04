<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

  // Everything here is drawn from what the server says each setting is (/api/settings/app): labels, help, limits, whether a
  // change applies at once or after a restart. Nothing is hard-coded, so this page cannot disagree with the server.
  let data = $state(null)
  let form = $state({})          // what is in the boxes, as text
  let resets = $state({})        // fields marked "use the default"
  let errors = $state({})
  let message = $state('')
  let problem = $state('')
  let saving = $state(false)
  let restarting = $state(false)

  // (a number box hands back a NUMBER, not text: always compare and convert as text)
  const text = (f) => String(form[f.name] ?? '')
  const show = (f, stored) => (f.scale && f.scale !== 1 ? String(+(stored / f.scale).toPrecision(6)) : String(stored))
  const toStored = (f, text) => {
    if (f.kind === 'int' || f.kind === 'float') {
      const n = Number(text)
      if (text.trim() === '' || Number.isNaN(n)) return text           // the server explains what is wrong with it
      const v = n * (f.scale || 1)
      return f.kind === 'int' ? Math.round(v) : v
    }
    return text
  }

  function fill(payload) {
    data = payload
    const next = {}
    for (const f of payload.fields) next[f.name] = show(f, f.saved ?? f.value)
    form = next; resets = {}; errors = {}
  }
  async function load() {
    try { fill(await api.get('/api/settings/app')) } catch (e) { problem = e.message }
  }
  onMount(load)
  // keep the usage numbers fresh while the page is open
  onMount(() => {
    const t = setInterval(async () => { if (!saving && data) try { data.status = (await api.get('/api/settings/app')).status } catch { /* ignore */ } }, 15000)
    return () => clearInterval(t)
  })

  const changed = $derived.by(() => {
    if (!data) return []
    return data.fields.filter((f) => resets[f.name] ? f.saved !== null && f.saved !== undefined : text(f) !== show(f, f.saved ?? f.value))
  })

  function applyPreset(key) {
    const p = data.presets[key]
    message = ''; errors = {}
    for (const f of data.fields) {
      if (f.locked_by_env) continue
      if (p.values === null) {                         // "built-in defaults": every setting back to its default
        resets[f.name] = true; form[f.name] = show(f, f.default)
      } else if (f.name in p.values) {
        resets[f.name] = false; form[f.name] = show(f, p.values[f.name])
      }
    }
    message = `"${p.label}" filled in below. Nothing changes until you press Save.`
  }
  function useDefault(f) { resets[f.name] = true; form[f.name] = show(f, f.default) }
  function edited(f) { resets[f.name] = false }

  async function save() {
    saving = true; message = ''; problem = ''; errors = {}
    const values = {}
    for (const f of changed) values[f.name] = resets[f.name] ? null : toStored(f, text(f))
    try {
      fill(await api.put('/api/settings/app', { values }))
      message = Object.keys(values).length ? 'Saved.' : 'Nothing to save.'
    } catch (e) {
      problem = e.message
      // the server says which field is wrong; show it next to that field
      try { const d = JSON.parse(e.rawDetail ?? 'null'); if (d?.field) errors[d.field] = d.message } catch { /* not structured */ }
    } finally {
      saving = false
    }
  }

  async function restart() {
    restarting = true; problem = ''
    try { await api.post('/api/system/restart') } catch (e) { problem = e.message; restarting = false; return }
    await new Promise((r) => setTimeout(r, 3500))
    for (let i = 0; i < 40; i++) {                      // wait for it to come back, then reload the page
      try { await fetch('/api/auth/status', { cache: 'no-store' }); location.reload(); return } catch { /* still down */ }
      await new Promise((r) => setTimeout(r, 1500))
    }
    problem = 'newtube has not come back after a minute. Check it with: systemctl --user status newtube'
    restarting = false
  }

  const pct = (n, cap) => (cap > 0 ? Math.min(100, Math.round((n / cap) * 100)) : 0)
</script>

{#if problem && !data}<p class="error">{problem}</p>{/if}
{#if data}
  <section class="app" aria-label="Newtube settings">
    <h2>YouTube traffic and system</h2>
    <p class="muted">
      These used to live in a file. Changes marked <span class="tag now">applies now</span> take effect the moment you save;
      <span class="tag later">after restart</span> ones need newtube restarted.
    </p>

    {#if data.status.bot_check.active}
      <div class="bot" role="status">
        <b>YouTube is currently asking this server for a bot check.</b>
        Background lookups are paused (about {data.status.bot_check.background_paused_minutes} more minutes) and a click is allowed
        to try again about once a minute. Nothing here needs changing: newtube is already staying quiet.
      </div>
    {/if}

    <div class="usage" aria-label="Today's traffic">
      {#each [['Imported videos looked up (24 h)', data.status.usage.backfill_24h, data.status.usage.backfill_cap],
              ['Related-video lookups (24 h)', data.status.usage.related_24h, data.status.usage.related_cap],
              ['Channel-page lookups (this hour)', data.status.usage.channel_lookups_hour, data.status.usage.channel_lookups_cap],
              ['All background requests today', data.status.usage.background_today, data.status.usage.background_cap]] as [label, n, cap]}
        <div class="meter"><span>{label}</span><b>{n} of {cap}</b><i><u style:width={pct(n, cap) + '%'}></u></i></div>
      {/each}
    </div>

    <div class="presets" role="group" aria-label="Presets">
      {#each Object.entries(data.presets) as [key, p]}
        <button type="button" class="btn" title={p.help} onclick={() => applyPreset(key)}>{p.label}</button>
      {/each}
    </div>

    {#each data.groups as g}
      <h3>{g.title}</h3>
      <p class="muted small">{g.help}</p>
      {#each data.fields.filter((f) => f.group === g.id) as f (f.name)}
        <div class="field" class:bad={errors[f.name]}>
          <label for={'set-' + f.name}>
            {f.label}
            <span class="tag" class:now={f.live} class:later={!f.live}>{f.live ? 'applies now' : 'after restart'}</span>
            {#if f.pending_restart}<span class="tag pending">saved, waiting for restart</span>{/if}
            {#if f.locked_by_env}<span class="tag lock">fixed by environment variable</span>{/if}
          </label>
          <div class="control">
            {#if f.kind === 'choice'}
              <select id={'set-' + f.name} bind:value={form[f.name]} onchange={() => edited(f)} disabled={f.locked_by_env}>
                {#each f.choices as c}<option value={c}>{c}</option>{/each}
              </select>
            {:else if f.kind === 'text'}
              <input id={'set-' + f.name} class="input wide" bind:value={form[f.name]} oninput={() => edited(f)}
                     placeholder={f.placeholder} disabled={f.locked_by_env} autocomplete="off" spellcheck="false" />
            {:else}
              <input id={'set-' + f.name} class="input num" type="number" step={f.kind === 'int' && f.scale === 1 ? 1 : 'any'}
                     bind:value={form[f.name]} oninput={() => edited(f)} disabled={f.locked_by_env} />
              {#if f.unit}<span class="muted">{f.unit}</span>{/if}
            {/if}
            {#if !f.locked_by_env && (resets[f.name] || text(f) !== show(f, f.default))}
              <button type="button" class="link" onclick={() => useDefault(f)}>use default ({f.kind === 'text' && !f.default ? 'empty' : show(f, f.default)})</button>
            {/if}
          </div>
          <p class="muted small">{f.help}</p>
          {#if errors[f.name]}<p class="error small" role="alert">{errors[f.name]}</p>{/if}
        </div>
      {/each}
    {/each}

    <div class="bar">
      <button class="btn primary" onclick={save} disabled={saving || !changed.length}>{saving ? 'Saving…' : 'Save'}</button>
      {#if changed.length}<span class="muted small">{changed.length} change{changed.length === 1 ? '' : 's'} not saved yet</span>{/if}
      <span class="ok" role="status" aria-live="polite">{message}</span>
      {#if problem}<span class="error small" role="alert">{problem}</span>{/if}
    </div>

    {#if data.pending_restart}
      <div class="restart" role="status">
        Some saved changes apply after newtube restarts.
        {#if data.can_restart}
          <button class="btn" onclick={restart} disabled={restarting}>{restarting ? 'Restarting…' : 'Restart newtube now'}</button>
        {:else}
          Stop it and start it again (if it runs as a service: <code>systemctl --user restart newtube</code>).
        {/if}
      </div>
    {/if}
    <p class="muted small">Not shown here, on purpose: the address and port newtube listens on and its folders. Changing them from this page could lock you out; they stay in <code>newtube.toml</code>.</p>
  </section>
{/if}

<style>
  .app { margin-bottom: 8px; }
  h3 { font-size: 15px; margin: 26px 0 2px; }
  .small { font-size: 13px; }
  .tag { font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px; margin-left: 6px; white-space: nowrap; vertical-align: middle; }
  .tag.now { background: color-mix(in srgb, #3fb950 22%, transparent); color: #3fb950; }
  .tag.later { background: color-mix(in srgb, #d29922 22%, transparent); color: #d29922; }
  .tag.pending { background: color-mix(in srgb, #d29922 35%, transparent); color: #d29922; }
  .tag.lock { background: var(--bg-hover); color: var(--text-dim); }
  .field { padding: 12px 0; border-bottom: 1px solid var(--border); }
  .field label { font-weight: 600; display: block; margin-bottom: 6px; }
  .field p { margin: 6px 0 0; }
  .control { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .input.num { width: 9em; }
  .input.wide { width: min(100%, 30em); }
  select, .input { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 8px; padding: 7px 10px; color: var(--text); }
  .field.bad .input, .field.bad select { border-color: var(--danger); }
  .link { background: none; border: 0; padding: 0; color: var(--accent); font-size: 13px; cursor: pointer; }
  .presets { display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0 4px; }
  .usage { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 10px; margin: 14px 0; }
  .meter { background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; display: grid; gap: 2px; font-size: 13px; }
  .meter b { font-size: 16px; }
  .meter i { display: block; height: 5px; background: var(--bg-hover); border-radius: 3px; overflow: hidden; margin-top: 4px; }
  .meter u { display: block; height: 100%; background: var(--accent); }
  .bot, .restart { background: var(--bg-elev); border: 1px solid #d29922; border-radius: var(--radius); padding: 10px 14px; margin: 12px 0; font-size: 14px; }
  .bar { position: sticky; bottom: 0; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; padding: 12px 0; background: var(--bg); margin-top: 8px; }
  .ok { color: #3fb950; font-size: 14px; }
</style>
