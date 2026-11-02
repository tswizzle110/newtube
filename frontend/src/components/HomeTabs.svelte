<script>
  import { router } from '../lib/router.svelte.js'
  const is = (p) => router.pathname === p
</script>

<nav class="tabs" aria-label="Home">
  <a href="/" class:active={is('/')}>Home</a>
  <a href="/subscriptions" class:active={is('/subscriptions')}>Subscriptions</a>
</nav>

<style>
  .tabs { display: flex; gap: 4px; margin: 0 0 18px; border-bottom: 1px solid var(--border, rgba(128, 128, 128, 0.3)); }
  a { padding: 8px 14px; color: var(--text-dim); border-bottom: 2px solid transparent; margin-bottom: -1px; font-weight: 600; }
  a:hover { color: var(--text); }
  a.active { color: var(--text); border-bottom-color: var(--accent); }
</style>
