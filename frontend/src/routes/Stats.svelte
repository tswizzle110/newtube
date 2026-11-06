<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { spent } from '../lib/format.js'

  let s = $state(null)
  let error = $state('')
  let since = $state('')     // the date box
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone   // so "a day" is YOUR day

  async function load() {
    try {
      s = await api.get(`/api/stats/watch?tz=${encodeURIComponent(tz)}`)
      since = s.since
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(() => { document.title = 'Watch time · newtube'; load() })

  async function saveSince(value) {
    try {
      await api.put('/api/stats/since', { since: value })
      await load()
    } catch (e) {
      error = e.message
    }
  }

  const label = (iso) => {
    const [y, m, d] = iso.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  }
  const longest = $derived(Math.max(1, ...(s?.days ?? []).map((d) => d.seconds)))
  const rows = $derived([...(s?.days ?? [])].reverse())          // newest first
</script>

<div class="page narrow">
  <h1>Watch time</h1>
  {#if error}<p class="error">{error}</p>{/if}
  {#if s}
    <div class="hero">
      <div class="big" aria-label="Total watch time">{spent(s.total_seconds)}</div>
      <div class="muted">
        watched since {label(s.since)} · {s.videos.toLocaleString()} video{s.videos === 1 ? '' : 's'} in
        {s.sessions.toLocaleString()} session{s.sessions === 1 ? '' : 's'}
      </div>
    </div>

    <div class="facts">
      <div><b>{spent(s.average_per_day)}</b><span class="muted">average per day ({s.days_counted} day{s.days_counted === 1 ? '' : 's'})</span></div>
      <div><b>{s.active_days}</b><span class="muted">day{s.active_days === 1 ? '' : 's'} you watched</span></div>
      <div><b>{s.longest_day ? spent(s.longest_day.seconds) : '–'}</b><span class="muted">{s.longest_day ? `longest day (${label(s.longest_day.date)})` : 'longest day'}</span></div>
    </div>

    {#if s.skipped.count}
      <div class="skipped">
        <b>{spent(s.skipped.seconds)}</b> skipped by SponsorBlock
        <span class="muted">· {s.skipped.count.toLocaleString()} skip{s.skipped.count === 1 ? '' : 's'}{#if s.skipped.categories.length}
          · {s.skipped.categories.map((c) => `${c.category.replace('_', ' ')} ${spent(c.seconds)}`).join(', ')}{/if}</span>
      </div>
    {/if}

    <form class="since" onsubmit={(e) => { e.preventDefault(); saveSince(since) }}>
      <label>Counting from <input type="date" bind:value={since} aria-label="Counting from" max={s.today} /></label>
      <button class="btn">Use this date</button>
      {#if !s.since_is_automatic}<button type="button" class="btn" onclick={() => saveSince(null)}>Back to automatic</button>{/if}
      <span class="muted small">{s.since_is_automatic ? 'Automatic: from your first recorded watch.' : 'Set by you.'}</span>
    </form>

    <h2>By day</h2>
    <table class="days">
      <tbody>
        {#each rows as d (d.date)}
          <tr class:empty={!d.seconds}>
            <th scope="row">{d.date === s.today ? 'Today' : label(d.date)}</th>
            <td class="barcell"><span class="bar" style:width={(d.seconds / longest) * 100 + '%'}></span></td>
            <td class="num">{d.seconds ? spent(d.seconds) : '–'}{#if d.skipped}<span class="muted sk" title="skipped by SponsorBlock"> · {spent(d.skipped)} skipped</span>{/if}</td>
          </tr>
        {/each}
      </tbody>
      <tfoot><tr><th scope="row">Total</th><td></td><td class="num">{spent(s.total_seconds)}</td></tr></tfoot>
    </table>

    {#if s.top_channels.length}
      <h2>Most watched channels</h2>
      <ol class="top">
        {#each s.top_channels as c}<li><a href={`/channel/${c.id}`}>{c.title || c.id}</a><span class="muted">{spent(c.seconds)}</span></li>{/each}
      </ol>
    {/if}
    {#if s.top_videos.length}
      <h2>Most watched videos</h2>
      <ol class="top">
        {#each s.top_videos as v}<li><a href={`/watch?v=${v.id}&src=direct`}>{v.title}</a><span class="muted">{spent(v.seconds)}</span></li>{/each}
      </ol>
    {/if}

    <p class="muted small note">
      This counts time actually spent playing videos in newtube, as video time (at 2× speed an hour of video takes half
      an hour of your day but counts as an hour). Parts SponsorBlock skipped are not watch time (they are totalled separately, minus any skips you undid), and replays inside one viewing are not double-counted.
      Your imported Google history is not included: it only says a video was opened, not for how long. Days are in your
      time zone ({s.tz}). Stays on this server.
    </p>
  {/if}
</div>

<style>
  .narrow { max-width: 760px; }
  h2 { font-size: 16px; margin: 28px 0 10px; }
  .hero { margin: 4px 0 20px; }
  .big { font-size: 52px; font-weight: 800; letter-spacing: -1px; line-height: 1.1; }
  .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .facts div { background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; display: flex; flex-direction: column; gap: 2px; }
  .facts b { font-size: 22px; }
  .facts span, .small { font-size: 13px; }
  .skipped { margin: 0 0 18px; }
  .skipped b { font-size: 18px; }
  .sk { font-size: 12px; }
  .since { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .since label { display: flex; gap: 8px; align-items: center; }
  .since input { background: var(--bg-elev); border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; }
  .days { width: 100%; border-collapse: collapse; }
  .days th { text-align: left; font-weight: 500; padding: 6px 10px 6px 0; white-space: nowrap; }
  .days td { padding: 6px 0; }
  .days .barcell { width: 100%; padding-right: 12px; }
  .days .bar { display: block; height: 14px; min-width: 2px; background: var(--accent); border-radius: 4px; }
  .days tr.empty .bar { display: none; }
  .days tr.empty th, .days tr.empty .num { color: var(--text-dim); }
  .days .num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .days tfoot th, .days tfoot .num { border-top: 1px solid var(--border); font-weight: 700; padding-top: 10px; }
  .top { list-style: none; margin: 0; padding: 0; }
  .top li { display: flex; justify-content: space-between; gap: 16px; padding: 6px 0; border-bottom: 1px solid var(--border); }
  .top a { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .note { margin-top: 28px; }
</style>
