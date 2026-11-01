export function duration(sec) {
  if (sec == null) return ''
  sec = Math.floor(sec)
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return (h ? h + ':' : '') + mm + ':' + String(s).padStart(2, '0')
}

const UNITS = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400],
  ['hour', 3600], ['minute', 60]]

export function ago(ts) {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  for (const [name, secs] of UNITS) {
    if (diff >= secs) {
      const n = Math.floor(diff / secs)
      return `${n} ${name}${n > 1 ? 's' : ''} ago`
    }
  }
  return 'just now'
}

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
export const views = (n) => (n == null ? '' : compact.format(n) + ' views')

const subsFmt = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
export const subscribers = (n) => (n == null ? '' : subsFmt.format(n) + ' subscribers')

// Avatars / playlist thumbnails of things we don't have locally go through our image proxy.
export const img = (u) => (u ? '/api/img?u=' + encodeURIComponent(u) : '')
