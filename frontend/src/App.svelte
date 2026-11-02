<script>
  import { onMount, tick } from 'svelte'
  import { router, navigate, interceptLinks } from './lib/router.svelte.js'
  import { auth, checkAuth, logout } from './lib/auth.svelte.js'
  import { nav, setCollapsed, loadSubs, loadLists } from './lib/nav.svelte.js'
  import Login from './routes/Login.svelte'
  import Feed from './routes/Feed.svelte'
  import Home from './routes/Home.svelte'
  import MyList from './routes/MyList.svelte'
  import History from './routes/History.svelte'
  import Channels from './routes/Channels.svelte'
  import Watch from './routes/Watch.svelte'
  import Search from './routes/Search.svelte'
  import Channel from './routes/Channel.svelte'
  import Playlist from './routes/Playlist.svelte'
  import Settings from './routes/Settings.svelte'
  import Sidebar from './components/Sidebar.svelte'

  let sidebar = $state()

  onMount(checkAuth)
  let started = false
  $effect(() => {
    if (!auth.loggedIn) return
    loadSubs(); loadLists()                        // the sidebar's channel list and your lists
    if (!started) {                                // only when newtube is first opened, never on later visits to Home
      started = true
      if (router.pathname === '/for-you') navigate('/', { replace: true })           // the old address of Home
      else if (router.pathname === '/' && nav.startPage === 'subscriptions') navigate('/subscriptions', { replace: true })
    }
  })

  let theme = $state(readTheme())
  function readTheme() {
    try { return localStorage.getItem('theme') ?? 'dark' } catch { return 'dark' }
  }
  $effect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('theme', theme) } catch { /* private mode */ }
  })

  const is = (p) => router.pathname === p

  // `/` jumps to the search box from anywhere (like YouTube).
  async function onKey(e) {
    if (e.key === '/' && !e.target.closest?.('input, textarea, select, [contenteditable]')) {
      e.preventDefault()
      if (nav.collapsed) { setCollapsed(false); await tick() }   // a hidden sidebar isn't on the page at all
      sidebar?.focusSearch()
    }
  }
</script>

<svelte:window onclick={interceptLinks} onkeydown={onKey} />

{#if !auth.ready}
  <!-- checking session -->
{:else if !auth.loggedIn}
  <Login />
{:else}
  <div class="layout" class:collapsed={nav.collapsed}>
    {#if !nav.collapsed}
      <Sidebar bind:this={sidebar} {theme} ontheme={() => (theme = theme === 'dark' ? 'light' : 'dark')} onlogout={logout} />
    {:else}
      <button class="reopen" title="Show the sidebar" aria-label="Show the sidebar" onclick={() => setCollapsed(false)}>☰</button>
    {/if}
    <main>
      {#key router.path}
        {#if is('/')}
          <Home />
        {:else if is('/subscriptions')}
          <Feed />
        {:else if is('/history')}
          <History />
        {:else if router.pathname.startsWith('/list/')}
          <MyList />
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
          <div class="page"><h1>Not found</h1><a href="/" class="muted">Back home</a></div>
        {/if}
      {/key}
    </main>
  </div>
{/if}

<style>
  .layout { display: grid; grid-template-columns: 256px minmax(0, 1fr); min-height: 100vh; }
  .layout.collapsed { grid-template-columns: minmax(0, 1fr); }
  main { min-width: 0; }
  .reopen { position: fixed; top: 10px; left: 10px; z-index: 20; background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 10px; color: var(--text-dim); }
  .reopen:hover { background: var(--bg-hover); color: var(--text); }
</style>
