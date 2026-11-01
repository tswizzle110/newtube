// Tiny history-API router. `router.path` is reactive.
let current = $state(location.pathname + location.search)

export const router = {
  get path() { return current },
  get pathname() { return current.split('?')[0] },
  get query() { return new URLSearchParams(current.split('?')[1] ?? '') },
}

export function navigate(to, { replace = false } = {}) {
  if (to === current) return
  replace ? history.replaceState({}, '', to) : history.pushState({}, '', to)
  current = to
  window.scrollTo(0, 0)
}

window.addEventListener('popstate', () => {
  current = location.pathname + location.search
})

// Turn clicks on in-app <a href="/..."> links into client-side navigation.
export function interceptLinks(e) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  const a = e.target.closest?.('a[href^="/"]')
  if (!a || a.target === '_blank' || a.hasAttribute('download')) return
  e.preventDefault()
  navigate(a.getAttribute('href'))
}
