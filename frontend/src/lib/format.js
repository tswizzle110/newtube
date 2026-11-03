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

export const compactNumber = (n) => (n == null ? '' : compact.format(n))

// Split comment text into plain text, clickable timestamps ("1:23" seeks the video) and links.
export function linkify(text) {
  const out = []
  const re = /(https?:\/\/[^\s<>"']+)|\b((?:\d{1,2}:)?\d{1,2}:\d{2})\b/g
  let last = 0, m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ t: 'text', v: text.slice(last, m.index) })
    if (m[1]) {
      out.push({ t: 'url', v: m[1] })
    } else {
      const parts = m[2].split(':').map(Number)
      const secs = parts.reduce((acc, x) => acc * 60 + x, 0)
      out.push(parts.at(-1) < 60 ? { t: 'time', v: m[2], s: secs } : { t: 'text', v: m[2] })
    }
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ t: 'text', v: text.slice(last) })
  return out
}

/** A length of time spent, for statistics: "3h 42m", "42m 10s", "35s", "0s". */
export function spent(sec) {
  sec = Math.max(0, Math.round(sec ?? 0))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}
