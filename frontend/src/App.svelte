<script>
  import { onMount } from 'svelte'
  import { router, interceptLinks } from './lib/router.svelte.js'
  import { auth, checkAuth, logout } from './lib/auth.svelte.js'
  import Login from './routes/Login.svelte'
  import Feed from './routes/Feed.svelte'
  import Channels from './routes/Channels.svelte'
  import Watch from './routes/Watch.svelte'

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
</script>

<svelte:window onclick={interceptLinks} />

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
    </nav>
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
</style>
