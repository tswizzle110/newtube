// The play queue (kept on the server, so a phone and a computer share it).
import { api } from './api.js'
import { say } from './toast.svelte.js'

export const queue = $state({ count: 0, videos: [], loaded: false })

export async function loadQueue() {
  try {
    const r = await api.get('/api/queue')
    queue.count = r.count; queue.videos = r.videos; queue.loaded = true
  } catch { /* keep what we had */ }
}

/** Queue a video; `next` puts it in front of everything already queued. */
export async function enqueue(video, next = false) {
  try {
    const r = await api.post('/api/queue', { video_id: video.id, next })
    say(r.added ? (next ? 'Playing next' : 'Added to the queue') : 'Already in the queue')
    await loadQueue()
  } catch (e) { say(e.message) }
}

export async function dequeue(id) {
  try { await api.del(`/api/queue/${id}`) } catch { /* it was not there */ }
  await loadQueue()
}
