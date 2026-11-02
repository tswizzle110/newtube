<script>
  import { api } from '../lib/api.js'
  import { COLORS } from '../lib/sponsorblock.js'

  // Shows what SponsorBlock knows about this video, and lets you override actions for this channel.
  let { data, settings, channelId, channelTitle = '', onsettings } = $props()

  const ACTION_LABELS = { auto: 'Skip automatically', button: 'Show skip button', mark: 'Only mark on the bar', ignore: 'Ignore' }
  const cats = $derived([...new Set((data?.segments ?? []).map((s) => s.category))])
  const counts = $derived(Object.fromEntries(cats.map((c) => [c, data.segments.filter((s) => s.category === c).length])))
  const override = $derived(settings?.channels?.[channelId] ?? {})
  let saving = $state(false)
  let error = $state('')

  async function setOverride(cat, value) {
    const channels = { ...(settings.channels ?? {}) }
    const mine = { ...(channels[channelId] ?? {}) }
    if (value === 'default') delete mine[cat]; else mine[cat] = value
    if (Object.keys(mine).length) channels[channelId] = mine; else delete channels[channelId]
    saving = true; error = ''
    try {
      const saved = await api.put('/api/settings/sponsorblock', { ...settings, channels })
      onsettings({ ...settings, ...saved })
    } catch (e) {
      error = e.message
    } finally {
      saving = false
    }
  }
</script>

{#if data && (data.full_labels.length || cats.length)}
  <section class="sb">
    {#each data.full_labels as c}
      <div class="fulllabel" style:border-color={COLORS[c]}>
        This whole video is marked by SponsorBlock as: <b>{settings?.category_labels?.[c] ?? c}</b>
      </div>
    {/each}
    {#if cats.length}
      <div class="summary">
        <span class="muted">SponsorBlock:</span>
        {#each cats as c}
          <span class="chip"><i style:background={COLORS[c]}></i>{settings?.category_labels?.[c] ?? c} × {counts[c]}</span>
        {/each}
      </div>
      <details>
        <summary class="muted">Settings for {channelTitle || 'this channel'}</summary>
        <table>
          <tbody>
            {#each cats as c}
              <tr>
                <td><i style:background={COLORS[c]}></i>{settings?.category_labels?.[c] ?? c}</td>
                <td>
                  <select disabled={saving} value={override[c] ?? 'default'} onchange={(e) => setOverride(c, e.target.value)}>
                    <option value="default">Default ({ACTION_LABELS[settings?.categories?.[c]] ?? '?'})</option>
                    {#each Object.entries(ACTION_LABELS) as [k, label]}<option value={k}>{label}</option>{/each}
                  </select>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        {#if error}<p class="error">{error}</p>{/if}
      </details>
    {/if}
  </section>
{/if}

<style>
  .sb { margin: 12px 0 0; display: flex; flex-direction: column; gap: 8px; }
  .fulllabel { border-left: 4px solid; background: var(--bg-elev); padding: 8px 12px; border-radius: 6px; }
  .summary { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 13px; }
  .chip { display: inline-flex; gap: 6px; align-items: center; background: var(--bg-elev); padding: 2px 10px; border-radius: 999px; }
  i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
  .chip i { margin: 0; }
  details { font-size: 13px; } summary { cursor: pointer; }
  table { margin-top: 8px; border-spacing: 0 6px; } td { padding-right: 16px; }
  select { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; color: var(--text); }
</style>
