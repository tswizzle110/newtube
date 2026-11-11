// Svelte action: call `fn` when this element gets near the bottom of the screen. Put it on an empty element after a
// list and the next page loads by itself as you scroll, instead of waiting for a "Load more" click.
export function nearEnd(node, fn) {
  let call = fn
  const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) call() }, { rootMargin: '800px 0px' })
  io.observe(node)
  return { update(next) { call = next }, destroy() { io.disconnect() } }
}
