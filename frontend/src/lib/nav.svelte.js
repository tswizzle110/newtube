// Sidebar state (remembered between visits) and the list of subscribed channels it shows.
import { api } from './api.js'

function read(key, fallback) {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === '1'
  } catch { return fallback }
}
function readStr(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function write(key, value) {
  try { localStorage.setItem(key, value ? '1' : '0') } catch { /* private mode */ }
}

export const nav = $state({
  collapsed: window.innerWidth < 900 ? true : read('sidebarCollapsed', false), // a phone always starts with the menu closed
  subsOpen: read('subsOpen', false),
  startPage: readStr('startPage', 'home'),                  // which page newtube opens on: 'home' or 'subscriptions'
})
export const subs = $state({ list: [], loaded: false })

// (On a narrow screen the sidebar is a slide-over menu that closes after every move: that is not a preference.)
export function setCollapsed(v) { nav.collapsed = v; if (window.innerWidth >= 900) write('sidebarCollapsed', v) }
export function setSubsOpen(v) { nav.subsOpen = v; write('subsOpen', v) }
export function setStartPage(v) {
  nav.startPage = v
  try { localStorage.setItem('startPage', v) } catch { /* private mode */ }
}

/** (Re)load the channel list; call after subscribing or unsubscribing. */
export async function loadSubs() {
  try {
    subs.list = await api.get('/api/subscriptions')
    subs.loaded = true
  } catch { /* the sidebar just keeps what it had */ }
}

// Your own lists (Liked videos, Watch later, playlists you made) as shown in the sidebar.
export const mylists = $state({ list: [], loaded: false })

/** (Re)load your lists and their counts; call after anything that adds to or removes from one. */
export async function loadLists() {
  try {
    mylists.list = await api.get('/api/mylists')
    mylists.loaded = true
  } catch { /* keep what we had */ }
}
