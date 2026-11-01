<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { trackEvent } from '../lib/tracker.js'
  import { img, compactNumber, linkify } from '../lib/format.js'

  // Loaded only when scrolled into view. player: { seekTo(seconds) } so timestamps in comments work.
  let { videoId, player = null } = $props()

  let sentinel
  let sort = $state('top')
  let limit = $state(20)
  let data = $state(null) // { comments, has_more, total_estimate, max_reply_threads }
  let loading = $state(false)
  let error = $state('')
  let replies = $state({}) // comment id -> { open, loading, items, truncated, error }
  let ticket = 0 // guards against a slow response for an old sort overwriting a newer one

  async function load() {
    const mine = ++ticket
    loading = true; error = ''
    try {
      const r = await api.get(`/api/comments/${videoId}?sort=${sort}&limit=${limit}`)
      if (mine === ticket) data = r
    } catch (e) {
      if (mine === ticket) error = e.message
    } finally {
      if (mine === ticket) loading = false
    }
  }

  onMount(() => {
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return
      io.disconnect()
      trackEvent('comments_opened', { video_id: videoId })
      load()
    }, { rootMargin: '0px' }) // only when it actually scrolls into view
    io.observe(sentinel)
    return () => io.disconnect()
  })

  function setSort(s) {
    if (s === sort) return
    sort = s; limit = 20; data = null; replies = {}
    load()
  }
  function more() { limit = Math.min(limit + 20, 100); load() }

  async function toggle(c) {
    const st = replies[c.id]
    if (st?.open) { replies[c.id] = { ...st, open: false }; return }
    if (st?.items) { replies[c.id] = { ...st, open: true }; return }
    replies[c.id] = { open: true, loading: true }
    try {
      const r = await api.get(`/api/comments/${videoId}/replies/${c.id}?sort=${sort}`)
      replies[c.id] = { open: true, items: r.replies, truncated: r.truncated }
    } catch (e) {
      replies[c.id] = { open: true, error: e.message }
    }
  }

  function seek(s) {
    player?.seekTo(s)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
</script>

{#snippet body(c, idx)}
  <div class="avatar">
    {#if c.author_thumbnail}<img src={img(c.author_thumbnail)} alt="" loading="lazy" />{:else}<span>{(c.author || '?')[0]}</span>{/if}
  </div>
  <div class="main">
    <div class="head">
      {#if c.is_pinned}<span class="badge" title="Pinned">📌</span>{/if}
      {#if c.author_id?.startsWith('UC')}
        <a class="author" class:uploader={c.is_uploader} href={`/channel/${c.author_id}`}>{c.author}</a>
      {:else}
        <span class="author" class:uploader={c.is_uploader}>{c.author}</span>
      {/if}
      {#if c.is_verified}<span class="badge" title="Verified">✓</span>{/if}
      <span class="muted small">{c.time_text ?? ''}</span>
    </div>
    <div class="text">{#each linkify(c.text) as seg}{#if seg.t === 'time'}<button class="ts" onclick={() => seek(seg.s)}>{seg.v}</button>{:else if seg.t === 'url'}<a href={seg.v} target="_blank" rel="noopener noreferrer">{seg.v}</a>{:else}{seg.v}{/if}{/each}</div>
    <div class="meta muted small">
      {#if c.like_count}<span>👍 {compactNumber(c.like_count)}</span>{/if}
      {#if c.is_hearted}<span title="Liked by the creator">♥ creator</span>{/if}
      {#if idx >= 0 && idx < (data?.max_reply_threads ?? 10)}
        <button class="link" onclick={() => toggle(c)}>
          {replies[c.id]?.open ? 'Hide replies' : 'Replies'}
        </button>
      {/if}
    </div>
  </div>
{/snippet}

<section class="comments" bind:this={sentinel}>
  <header>
    <h2>Comments{#if data?.total_estimate} <span class="muted">· ~{compactNumber(data.total_estimate)}</span>{/if}</h2>
    <div class="sorts" role="group" aria-label="Sort comments">
      <button class:on={sort === 'top'} onclick={() => setSort('top')}>Top</button>
      <button class:on={sort === 'new'} onclick={() => setSort('new')}>Newest</button>
    </div>
  </header>

  {#if error}<p class="error">{error}</p>{/if}
  {#if loading && !data}<p class="muted">Loading comments…</p>{/if}
  {#if data && !data.comments.length && !loading}<p class="muted">No comments (they may be turned off).</p>{/if}

  {#if data}
    <ul>
      {#each data.comments as c, i (c.id)}
        <li class="comment">
          {@render body(c, i)}
          {#if replies[c.id]?.open}
            <div class="replies">
              {#if replies[c.id].loading}<p class="muted small">Loading replies…</p>{/if}
              {#if replies[c.id].error}<p class="error small">{replies[c.id].error}</p>{/if}
              {#if replies[c.id].items && !replies[c.id].items.length}<p class="muted small">No replies.</p>{/if}
              {#each replies[c.id].items ?? [] as r (r.id)}
                <div class="comment reply">{@render body(r, -1)}</div>
              {/each}
              {#if replies[c.id].truncated}<p class="muted small">Showing the first replies only.</p>{/if}
            </div>
          {/if}
        </li>
      {/each}
    </ul>
    {#if loading}<p class="muted">Loading…</p>{/if}
    {#if data.has_more && !loading}
      <p><button class="btn" onclick={more}>Load more comments</button></p>
    {/if}
    {#if data.comments.length > (data.max_reply_threads ?? 10)}
      <p class="muted small">Replies can be loaded for the first {data.max_reply_threads} comments.</p>
    {/if}
  {/if}
</section>

<style>
  .comments { margin-top: 28px; min-height: 120px; }
  header { display: flex; align-items: baseline; gap: 20px; margin-bottom: 14px; }
  h2 { font-size: 16px; margin: 0; }
  .sorts { display: flex; gap: 4px; }
  .sorts button { background: none; border: 0; padding: 4px 10px; border-radius: 999px; color: var(--text-dim); }
  .sorts button.on { background: var(--bg-hover); color: var(--text); }
  ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 18px; }
  .comment { display: grid; grid-template-columns: 40px 1fr; gap: 12px; }
  .reply { margin-top: 12px; grid-template-columns: 28px 1fr; }
  .replies { grid-column: 2; }
  .avatar img, .avatar span { width: 100%; aspect-ratio: 1; border-radius: 50%; object-fit: cover; display: grid;
    place-items: center; background: var(--bg-hover); font-weight: 700; }
  .head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .author { font-weight: 600; font-size: 13px; }
  .author.uploader { background: var(--bg-hover); padding: 0 6px; border-radius: 4px; }
  .badge { font-size: 12px; }
  .text { white-space: pre-wrap; word-break: break-word; margin: 2px 0 4px; }
  .text a, .ts { color: var(--accent); }
  .ts { background: none; border: 0; padding: 0; }
  .meta { display: flex; gap: 14px; align-items: center; }
  .small { font-size: 13px; }
  .link { background: none; border: 0; padding: 0; color: var(--text-dim); font-weight: 600; }
  .link:hover { color: var(--text); }
</style>
