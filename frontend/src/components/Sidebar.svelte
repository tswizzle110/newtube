<script>
  import { tick } from 'svelte'
  import { router, navigate } from '../lib/router.svelte.js'
  import { nav, subs, mylists, setCollapsed, setSubsOpen, loadLists } from '../lib/nav.svelte.js'
  import { api } from '../lib/api.js'
  import { queue } from '../lib/queue.svelte.js'

  let { theme, ontheme, onlogout } = $props()
  let searchEl = $state()
  let q = $state(new URLSearchParams(location.search).get('q') ?? '')

  const is = (p) => router.pathname === p
  const activeList = $derived(router.pathname.startsWith('/list/') ? router.pathname.split('/')[2] : null)
  let adding = $state(false)
  let broken = $state({})          // channels whose picture would not load: show their letter instead of an empty circle
  let newName = $state('')
  let addError = $state('')
  async function newPlaylist(e) {
    e.preventDefault()
    if (!newName.trim()) return
    try {
      const p = await api.post('/api/mylists', { name: newName })
      adding = false; newName = ''; addError = ''
      await loadLists()
      navigate(`/list/${p.id}`)
    } catch (err) {
      addError = err.message
    }
  }
  const listRef = (l) => (l.kind === 'user' ? String(l.id) : l.kind)
  const activeChannel = $derived(router.pathname.startsWith('/channel/') ? router.pathname.split('/')[2] : null)

  // Expose to App: `/` focuses the search box, opening the sidebar first if it is hidden.
  export async function focusSearch() {
    if (nav.collapsed) { setCollapsed(false); await tick() }
    searchEl?.focus(); searchEl?.select()
  }

  function doSearch(e) {
    e.preventDefault()
    const v = q.trim()
    if (v) navigate(`/search?q=${encodeURIComponent(v)}&kind=video`)
    searchEl?.blur()
  }

  // Clicking "Subscriptions" shows the feed AND opens the list under it; the arrow only toggles the list.
  function openSubscriptions(e) {
    e.preventDefault()
    setSubsOpen(true)
    navigate('/subscriptions')
  }
</script>

<aside class="side" aria-label="Main navigation">
  <div class="top">
    <button class="icon" title="Hide the sidebar" aria-label="Hide the sidebar" onclick={() => setCollapsed(true)}>☰</button>
    <a class="logo" href="/">newtube</a>
  </div>

  <form class="search" onsubmit={doSearch} role="search">
    <input class="input" bind:this={searchEl} bind:value={q} placeholder="Search  ( / )" aria-label="Search" maxlength="200"
           onkeydown={(e) => e.key === 'Escape' && e.target.blur()} />
  </form>

  <nav>
    <a class="item" class:active={is('/')} href="/">Home</a>
    <div class="row" class:active={is('/subscriptions')}>
      <a class="label" href="/subscriptions" onclick={openSubscriptions}>Subscriptions</a>
      <button class="chev" aria-expanded={nav.subsOpen} aria-controls="sub-list" title={nav.subsOpen ? 'Hide channels' : 'Show channels'}
              onclick={() => setSubsOpen(!nav.subsOpen)}>{nav.subsOpen ? '▾' : '▸'}</button>
    </div>

    {#if nav.subsOpen}
      <ul id="sub-list" class="subs">
        {#each subs.list as c (c.id)}
          <li>
            <a href={`/channel/${c.id}`} class:active={activeChannel === c.id} title={c.title}>
              {#if c.has_avatar && !broken[c.id]}
                <img src={`/api/avatar/${c.id}`} alt="" loading="lazy" onerror={() => (broken[c.id] = true)} />
              {:else}
                <span class="ph">{c.title[0]}</span>
              {/if}
              <span class="name">{c.title}</span>
            </a>
          </li>
        {/each}
        {#if subs.loaded && !subs.list.length}
          <li class="empty muted">No subscriptions yet</li>
        {/if}
      </ul>
    {/if}

    <div class="lib"><span>Library</span>
      <button class="plus" title="New playlist" aria-label="New playlist" aria-expanded={adding} onclick={() => (adding = !adding)}>＋</button>
    </div>
    {#if adding}
      <form class="newpl" onsubmit={newPlaylist}>
        <input class="input" bind:value={newName} placeholder="Playlist name" aria-label="New playlist name" maxlength="100"
               onkeydown={(e) => e.key === 'Escape' && (adding = false)} />
        {#if addError}<span class="error small">{addError}</span>{/if}
      </form>
    {/if}
    <a class="item" class:active={is('/history')} href="/history">History</a>
    <a class="item" class:active={is('/stats')} href="/stats">Watch time</a>
    {#if queue.count || is('/queue')}
      <a class="item" class:active={is('/queue')} href="/queue"><span class="lname">Queue</span>{#if queue.count}<span class="count muted">{queue.count.toLocaleString()}</span>{/if}</a>
    {/if}
    {#each mylists.list as l (l.id)}
      <a class="item" class:active={activeList === listRef(l)} href={`/list/${listRef(l)}`}>
        <span class="lname">{l.name}</span>{#if l.count}<span class="count muted">{l.count.toLocaleString()}</span>{/if}
      </a>
    {/each}
  </nav>

  <!-- The things about newtube itself, not your videos, together at the bottom. -->
  <div class="bottom">
    <a class="item" class:active={is('/settings')} href="/settings">Settings</a>
    <div class="account">
      <button class="btn" onclick={ontheme} title="Switch between dark and light" aria-label="Switch between dark and light">{theme === 'dark' ? '☀' : '☾'}</button>
      <button class="btn" onclick={onlogout}>Log out</button>
    </div>
  </div>
</aside>

<style>
  .side { position: sticky; top: 0; height: 100vh; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;
    padding: 12px 10px; background: var(--bg); border-right: 1px solid var(--border); }
  .top { display: flex; align-items: center; gap: 8px; padding: 0 4px; }
  .logo { font-weight: 800; font-size: 18px; letter-spacing: -0.5px; }
  .icon { background: none; border: 0; font-size: 18px; padding: 6px 8px; border-radius: 8px; color: var(--text-dim); }
  .icon:hover { background: var(--bg-hover); color: var(--text); }
  .search .input { width: 100%; }
  nav { display: flex; flex-direction: column; gap: 2px; flex: 1; min-height: 0; }
  .row, .item { display: flex; align-items: center; border-radius: 8px; color: var(--text-dim); }
  .row:hover, .item:hover { background: var(--bg-hover); color: var(--text); }
  .row.active, .item.active { background: var(--bg-hover); color: var(--text); }
  .label { flex: 1; padding: 8px 12px; }
  .item { padding: 8px 12px; }
  .lib { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px 2px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); }
  .plus { background: none; border: 0; color: var(--text-dim); font-size: 15px; padding: 0 4px; border-radius: 6px; }
  .plus:hover { color: var(--text); background: var(--bg-hover); }
  .newpl { padding: 4px 8px; display: flex; flex-direction: column; gap: 4px; }
  .newpl .input { width: 100%; }
  .small { font-size: 12px; }
  .lname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .count { font-size: 12px; margin-left: 8px; }
  .chev { background: none; border: 0; color: inherit; padding: 8px 12px; }
  .subs { list-style: none; margin: 2px 0 6px; padding: 0; display: flex; flex-direction: column; gap: 1px; overflow-y: auto; max-height: 55vh; }
  .subs a { display: flex; align-items: center; gap: 10px; padding: 6px 10px 6px 14px; border-radius: 8px; color: var(--text-dim); }
  .subs a:hover { background: var(--bg-hover); color: var(--text); }
  .subs a.active { background: var(--bg-hover); color: var(--text); font-weight: 600; }
  .subs img, .ph { width: 24px; height: 24px; border-radius: 50%; flex: none; object-fit: cover; }
  .ph { background: var(--bg-elev); display: grid; place-items: center; font-size: 12px; font-weight: 700; }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { padding: 6px 14px; font-size: 13px; }
  .bottom { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--border); padding-top: 10px; }
  .account { display: flex; gap: 8px; }
  .account .btn:last-child { flex: 1; }

  /* (after the base rules above, so these win) */
  @media (max-width: 899px) {      /* a phone: the sidebar slides over the page instead of sitting beside it */
    .side { position: fixed; z-index: 70; top: 0; left: 0; bottom: 0; height: auto; width: min(300px, 86vw);
      padding-top: calc(12px + env(safe-area-inset-top)); box-shadow: 0 0 30px rgba(0, 0, 0, 0.6); }
  }
  @media (pointer: coarse), (max-width: 899px) { .item, .row { min-height: 44px; } .subs a { min-height: 44px; } }
</style>
