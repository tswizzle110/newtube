<script>
  // A small "⋯" button that opens a list of actions. Used on video cards for the things you do rarely (not interested,
  // remove from this list, ...), so the grid stays clean. items: [{ label, onclick, danger? }]
  let { items = [], label = 'More actions' } = $props()
  let open = $state(false)
  let root = $state()
  let button = $state()

  function pick(item) {
    open = false
    item.onclick()
  }
  function onWindowClick(e) { if (open && root && !root.contains(e.target)) open = false }
  function onKey(e) {
    if (e.key === 'Escape' && open) { open = false; button?.focus() }
  }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKey} />

{#if items.length}
  <span class="menu" bind:this={root}>
    <button class="dots" bind:this={button} aria-haspopup="menu" aria-expanded={open} aria-label={label} title={label}
            onclick={() => (open = !open)}>⋯</button>
    {#if open}
      <ul class="pop" role="menu">
        {#each items as item}
          <li role="none">
            <button role="menuitem" class:danger={item.danger} onclick={() => pick(item)}>{item.label}</button>
          </li>
        {/each}
      </ul>
    {/if}
  </span>
{/if}

<style>
  .menu { position: relative; flex: none; }
  .dots { background: none; border: 0; width: 32px; height: 32px; border-radius: 50%; font-size: 20px; line-height: 1;
    color: var(--text-dim); display: grid; place-items: center; }
  .dots:hover, .dots[aria-expanded='true'] { background: var(--bg-hover); color: var(--text); }
  .pop { position: absolute; right: 0; top: 34px; z-index: 30; min-width: 190px; margin: 0; padding: 6px; list-style: none;
    background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4); }
  .pop button { width: 100%; text-align: left; background: none; border: 0; padding: 9px 12px; border-radius: 6px; white-space: nowrap; }
  .pop button:hover, .pop button:focus-visible { background: var(--bg-hover); }
  .pop button.danger:hover { color: var(--danger); }
  @media (pointer: coarse) { .dots { width: 44px; height: 44px; } .pop button { padding: 12px 14px; } }
</style>
