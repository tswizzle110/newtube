<script>
  import { onMount, tick, untrack } from 'svelte'
  import { router, navigate, interceptLinks } from './lib/router.svelte.js'
  import { net } from './lib/net.svelte.js'
  import { auth, checkAuth, logout } from './lib/auth.svelte.js'
  import { nav, setCollapsed, loadSubs, loadLists } from './lib/nav.svelte.js'
  import Login from './routes/Login.svelte'
  import Feed from './routes/Feed.svelte'
  import Home from './routes/Home.svelte'
  import MyList from './routes/MyList.svelte'
  import History from './routes/History.svelte'
  import Stats from './routes/Stats.svelte'
  import Channels from './routes/Channels.svelte'
  import Watch from './routes/Watch.svelte'
  import Search from './routes/Search.svelte'
  import Channel from './routes/Channel.svelte'
  import Playlist from './routes/Playlist.svelte'
  import Settings from './routes/Settings.svelte'
  import Queue from './routes/Queue.svelte'
  import { toast } from './lib/toast.svelte.js'
  import { loadQueue } from './lib/queue.svelte.js'
  import Sidebar from './components/Sidebar.svelte'

  let sidebar = $state()

  onMount(checkAuth)
  // The video being watched (kept when you leave /watch, which turns the page into the mini-player).
  let session = $state(null)
  let sessionKey = 0
  const onWatch = $derived(router.pathname === '/watch')
  const sessionHref = (s) => `/watch?v=${s.id}&src=${s.source}${s.list ? `&list=${s.list}` : ''}${s.mylist ? `&mylist=${s.mylist}` : ''}`
  $effect(() => {
    if (!auth.loggedIn) { untrack(() => (session = null)); return }          // logged out: nothing keeps playing
    if (router.pathname !== '/watch') return
    const q = router.query
    const id = q.get('v')
    if (!id) return
    const wanted = { id, source: q.get('src') ?? 'direct', list: q.get('list'), mylist: q.get('mylist') }
    untrack(() => {
      // The same video in the same list keeps its page (and its playback), e.g. when you expand the mini-player.
      // Another video, or the same video reached from a different list, gets a fresh page.
      const same = session && session.id === id && session.list === wanted.list && session.mylist === wanted.mylist
      session = same ? { ...session, source: wanted.source } : { ...wanted, key: ++sessionKey }
    })
  })
  // Autoplay of the next video in a list: on the video page that is a normal navigation; in the mini-player the
  // video just changes in place and you stay where you are.
  function goTo(href) {
    if (onWatch) return navigate(href)
    const q = new URL(href, location.origin).searchParams
    session = { id: q.get('v'), source: q.get('src') ?? 'direct', list: q.get('list'), mylist: q.get('mylist'), key: ++sessionKey }
  }

  // On a phone the sidebar is a slide-over menu: choosing something closes it.
  $effect(() => { router.path; if (window.innerWidth < 900) untrack(() => setCollapsed(true)) })

  let started = false
  $effect(() => {
    if (!auth.loggedIn) return
    loadSubs(); loadLists(); loadQueue()           // the sidebar's channel list, your lists and the queue
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

{#if toast.text}<div class="toast" role="status">{toast.text}</div>{/if}

{#if net.offline || net.unreachable}
  <div class="netbar" role="status">
    {net.offline ? "You're offline. Pages will load, and playback and your history will catch up, when the connection is back." : "Can't reach newtube right now. Trying again…"}
  </div>
{/if}

{#if !auth.ready}
  <!-- checking session -->
{:else if !auth.loggedIn}
  <Login />
{:else}
  <div class="layout" class:collapsed={nav.collapsed}>
    {#if !nav.collapsed}
      <button class="backdrop" aria-label="Close the menu" onclick={() => setCollapsed(true)}></button>
      <Sidebar bind:this={sidebar} {theme} ontheme={() => (theme = theme === 'dark' ? 'light' : 'dark')} onlogout={logout} />
    {:else}
      <button class="reopen" title="Show the sidebar" aria-label="Show the sidebar" onclick={() => setCollapsed(false)}>☰</button>
    {/if}
    <main>
      {#key router.path + ':' + net.epoch}
        {#if is('/')}
          <Home />
        {:else if is('/subscriptions')}
          <Feed />
        {:else if is('/stats')}
          <Stats />
        {:else if is('/history')}
          <History />
        {:else if router.pathname.startsWith('/list/')}
          <MyList />
        {:else if is('/queue')}
          <Queue />
        {:else if is('/channels')}
          <Channels />
        {:else if is('/search')}
          <Search />
        {:else if router.pathname.startsWith('/channel/')}
          <Channel />
        {:else if is('/playlist')}
          <Playlist />
        {:else if is('/watch')}
          <!-- the video page is rendered below, outside the routed pages, so that it can keep playing as a mini-player -->
        {:else if is('/settings')}
          <Settings />
        {:else}
          <div class="page"><h1>Not found</h1><a href="/" class="muted">Back home</a></div>
        {/if}
      {/key}
      <!-- The video page lives here, outside the routed pages, so it survives navigation: on /watch it IS the page,
           anywhere else it shrinks into a corner player that keeps playing. -->
      {#if session}
        {#key session.key}
          <div class="watchhost" class:mini={!onWatch}>
            <Watch id={session.id} source={session.source} list={session.list} mylist={session.mylist} mini={!onWatch}
                   onclose={() => (session = null)} onexpand={() => navigate(sessionHref(session))} onnavigate={goTo} />
          </div>
        {/key}
      {/if}
    </main>
  </div>
{/if}

<style>
  .layout { display: grid; grid-template-columns: 256px minmax(0, 1fr); min-height: 100vh; }
  .layout.collapsed { grid-template-columns: minmax(0, 1fr); }
  .watchhost.mini { position: fixed; right: 16px; bottom: 16px; z-index: 60; width: min(400px, calc(100vw - 32px));
    border-radius: 12px; overflow: hidden; background: #000; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.55); }
  main { min-width: 0; }
  .netbar { position: fixed; top: calc(8px + env(safe-area-inset-top)); left: 50%; transform: translateX(-50%); z-index: 90; max-width: min(92vw, 640px);
    background: var(--bg-elev); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 7px 16px; font-size: 13px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4); }
  .toast { position: fixed; left: 50%; bottom: calc(24px + env(safe-area-inset-bottom)); transform: translateX(-50%); z-index: 95; max-width: min(92vw, 420px);
    background: var(--text); color: var(--bg); border-radius: 8px; padding: 9px 16px; font-size: 14px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4); }
  .backdrop { display: none; }
  @media (max-width: 899px) {
    .layout, .layout.collapsed { grid-template-columns: minmax(0, 1fr); }
    .backdrop { display: block; position: fixed; inset: 0; z-index: 65; border: 0; background: rgba(0, 0, 0, 0.55); }
    .watchhost.mini { right: 8px; bottom: calc(8px + env(safe-area-inset-bottom)); width: min(320px, calc(100vw - 16px)); }
  }
  .reopen { position: fixed; top: calc(10px + env(safe-area-inset-top)); left: 10px; z-index: 20; background: var(--bg-elev); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 10px; color: var(--text-dim); }
  .reopen:hover { background: var(--bg-hover); color: var(--text); }
</style>
