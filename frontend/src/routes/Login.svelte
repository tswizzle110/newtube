<script>
  import { login } from '../lib/auth.svelte.js'

  let password = $state('')
  let error = $state('')
  let busy = $state(false)

  async function submit(e) {
    e.preventDefault()
    busy = true; error = ''
    try { await login(password) } catch (err) { error = err.message } finally { busy = false }
  }
</script>

<main class="wrap">
  <form onsubmit={submit}>
    <h1>newtube</h1>
    <!-- svelte-ignore a11y_autofocus -->
    <input class="input" type="password" placeholder="Password" bind:value={password} autofocus
           autocomplete="current-password" />
    <button class="btn primary" disabled={busy || !password}>Log in</button>
    {#if error}<p class="error">{error}</p>{/if}
  </form>
</main>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; }
  form { display: flex; flex-direction: column; gap: 12px; width: 280px; }
  h1 { text-align: center; font-size: 28px; }
</style>
