import { api } from './api.js'

export const auth = $state({ ready: false, loggedIn: false })

export async function checkAuth() {
  try {
    auth.loggedIn = (await api.get('/api/auth/status')).authenticated
  } finally {
    auth.ready = true
  }
}

export async function login(password) {
  await api.post('/api/login', { password })
  auth.loggedIn = true
}

export async function logout() {
  await api.post('/api/logout')
  auth.loggedIn = false
}
