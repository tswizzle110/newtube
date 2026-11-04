// Thin fetch wrapper. Everything talks to our own server only.
import { auth } from './auth.svelte.js'
import { net, refreshPages, whenOnline } from './net.svelte.js'

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const RETRY_STATUS = new Set([502, 503, 504])   // the server is restarting / a proxy is between us: pass in a moment
const GET_ATTEMPTS = 4                          // reading is safe to repeat; writing is not, so that is tried once

async function request(method, url, body) {
  const attempts = method === 'GET' ? GET_ATTEMPTS : 1
  for (let attempt = 0; ; attempt++) {
    let res
    try {
      res = await fetch(url, {
        method,
        headers: body !== undefined ? { 'content-type': 'application/json' } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    } catch {
      // No answer at all: no network, the wrong wifi, or the server is down. Wait, then try again.
      net.unreachable = navigator.onLine
      if (attempt >= attempts - 1) {
        net.failed = true
        throw new ApiError(0, navigator.onLine ? "Can't reach the server" : "You're offline")
      }
      if (navigator.onLine) await sleep(Math.min(8000, 1000 * 2 ** attempt) * (0.75 + Math.random() * 0.5))
      else await whenOnline()                    // offline: do not use up attempts, wait for the network
      continue
    }
    if (RETRY_STATUS.has(res.status) && attempt < attempts - 1) {
      net.unreachable = true
      await sleep(Math.min(8000, 1000 * 2 ** attempt) * (0.75 + Math.random() * 0.5))
      continue
    }
    net.unreachable = false
    if (net.failed && res.ok) refreshPages()     // we are talking to the server again: reload what failed to load
    if (res.status === 401 && !url.startsWith('/api/login') && !url.startsWith('/api/auth')) {
      auth.loggedIn = false // session expired: App shows the login page
    }
    if (!res.ok) {
      let msg = res.statusText
      try { msg = (await res.json()).detail ?? msg } catch { /* not JSON */ }
      throw new ApiError(res.status, msg)
    }
    return res.json()
  }
}

export const api = {
  get: (url) => request('GET', url),
  post: (url, body = {}) => request('POST', url, body),
  put: (url, body = {}) => request('PUT', url, body),
  patch: (url, body = {}) => request('PATCH', url, body),
  del: (url) => request('DELETE', url),
}
