<script>
  import { tick } from 'svelte'
  import { router, navigate } from '../lib/router.svelte.js'
  import { nav, subs, setCollapsed, setSubsOpen } from '../lib/nav.svelte.js'

  let { theme, ontheme, onlogout } = $props()
  let searchEl = $state()
  let q = $state(new URLSearchParams(location.search).get('q') ?? '')

  const is = (p) => router.pathname === p
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
    navigate('/')
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
    <div class="row" class:active={is('/')}>
      <a class="label" href="/" onclick={openSubscriptions}>Subscriptions</a>
      <button class="chev" aria-expanded={nav.subsOpen} aria-controls="sub-list" title={nav.subsOpen ? 'Hide channels' : 'Show channels'}
              onclick={() => setSubsOpen(!nav.subsOpen)}>{nav.subsOpen ? '▾' : '▸'}</button>
    </div>

    {#if nav.subsOpen}
      <ul id="sub-list" class="subs">
        {#each subs.list as c (c.id)}
          <li>
            <a href={`/channel/${c.id}`} class:active={activeChannel === c.id} title={c.title}>
              {#if c.has_avatar}
                <img src={`/api/avatar/${c.id}`} alt="" loading="lazy" />
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

    <a class="item" class:active={is('/channels')} href="/channels">Manage channels</a>
    <a class="item" class:active={is('/settings')} href="/settings">Settings</a>
  </nav>

  <div class="bottom">
    <button class="btn" onclick={ontheme} title="Toggle theme">{theme === 'dark' ? '☀' : '☾'}</button>
    <button class="btn" onclick={onlogout}>Log out</button>
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
  .chev { background: none; border: 0; color: inherit; padding: 8px 12px; }
  .subs { list-style: none; margin: 2px 0 6px; padding: 0; display: flex; flex-direction: column; gap: 1px; overflow-y: auto; max-height: 55vh; }
  .subs a { display: flex; align-items: center; gap: 10px; padding: 6px 10px 6px 14px; border-radius: 8px; color: var(--text-dim); }
  .subs a:hover { background: var(--bg-hover); color: var(--text); }
  .subs a.active { background: var(--bg-hover); color: var(--text); font-weight: 600; }
  .subs img, .ph { width: 24px; height: 24px; border-radius: 50%; flex: none; object-fit: cover; }
  .ph { background: var(--bg-elev); display: grid; place-items: center; font-size: 12px; font-weight: 700; }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { padding: 6px 14px; font-size: 13px; }
  .bottom { display: flex; gap: 8px; }
  .bottom .btn:last-child { flex: 1; }
</style>
