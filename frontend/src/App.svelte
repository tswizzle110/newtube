<script>
  import { onMount } from 'svelte'
  import { router, interceptLinks, navigate } from './lib/router.svelte.js'
  import { auth, checkAuth, logout } from './lib/auth.svelte.js'
  import Login from './routes/Login.svelte'
  import Feed from './routes/Feed.svelte'
  import Channels from './routes/Channels.svelte'
  import Watch from './routes/Watch.svelte'
  import Search from './routes/Search.svelte'
  import Channel from './routes/Channel.svelte'
  import Playlist from './routes/Playlist.svelte'
  import Settings from './routes/Settings.svelte'

  onMount(checkAuth)

  let theme = $state(readTheme())
  function readTheme() {
    try { return localStorage.getItem('theme') ?? 'dark' } catch { return 'dark' }
  }
  $effect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('theme', theme) } catch { /* private mode */ }
  })

  const is = (p) => router.pathname === p

  // Search box: `/` focuses it from anywhere (like YouTube), Enter searches videos.
  let searchEl = $state()
  let q = $state(new URLSearchParams(location.search).get('q') ?? '')
  function doSearch(e) {
    e.preventDefault()
    const v = q.trim()
    if (v) navigate(`/search?q=${encodeURIComponent(v)}&kind=video`)
    searchEl?.blur()
  }
  function onKey(e) {
    if (e.key === '/' && !e.target.closest?.('input, textarea, select, [contenteditable]')) {
      e.preventDefault()
      searchEl?.focus(); searchEl?.select()
    }
  }
</script>

<svelte:window onclick={interceptLinks} onkeydown={onKey} />

{#if !auth.ready}
  <!-- checking session -->
{:else if !auth.loggedIn}
  <Login />
{:else}
  <header>
    <a class="logo" href="/">newtube</a>
    <nav>
      <a href="/" class:active={is('/')}>Subscriptions</a>
      <a href="/channels" class:active={is('/channels')}>Channels</a>
      <a href="/settings" class:active={is('/settings')}>Settings</a>
    </nav>
    <form class="search" onsubmit={doSearch} role="search">
      <input class="input" bind:this={searchEl} bind:value={q} placeholder="Search  ( / )" aria-label="Search"
             maxlength="200" onkeydown={(e) => e.key === 'Escape' && e.target.blur()} />
    </form>
    <span class="spacer"></span>
    <button class="btn" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')} title="Toggle theme">
      {theme === 'dark' ? '☀' : '☾'}
    </button>
    <button class="btn" onclick={logout}>Log out</button>
  </header>
  {#key router.path}
    {#if is('/')}
      <Feed />
    {:else if is('/channels')}
      <Channels />
    {:else if is('/watch')}
      <Watch />
    {:else if is('/search')}
      <Search />
    {:else if router.pathname.startsWith('/channel/')}
      <Channel />
    {:else if is('/playlist')}
      <Playlist />
    {:else if is('/settings')}
      <Settings />
    {:else}
      <div class="page"><h1>Not found</h1><a href="/" class="muted">Back to subscriptions</a></div>
    {/if}
  {/key}
{/if}

<style>
  header { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; gap: 20px;
    height: 56px; padding: 0 24px; background: var(--bg); border-bottom: 1px solid var(--border); }
  .logo { font-weight: 800; font-size: 18px; letter-spacing: -0.5px; }
  nav { display: flex; gap: 6px; }
  nav a { padding: 6px 12px; border-radius: 8px; color: var(--text-dim); }
  nav a:hover { background: var(--bg-hover); color: var(--text); }
  nav a.active { color: var(--text); background: var(--bg-hover); }
  .spacer { flex: 1; }
  .search { flex: 0 1 460px; }
  .search .input { width: 100%; }
</style>
