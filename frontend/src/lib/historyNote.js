import { duration as fmt } from './format.js'

/** What to say about one History entry, from what is actually known (so a half-watched video is never "watched").
 *  - finished in newtube (90% played, or stopped at the very end): "Finished · 3:42 PM"
 *  - stopped part-way in newtube: "Stopped at 12:30 of 25:00 (50%) · 3:42 PM"
 *  - nothing but a date, e.g. from your imported Google history: "Opened 3:42 PM" (we cannot know how much you saw) */
export function historyNote(v, time) {
  const at = time(v.watched_at)
  if (v.completed) return `Finished · ${at}`
  if (v.resume_position > 0 && v.duration > 0) {
    const pct = Math.min(99, Math.max(1, Math.round((v.resume_position / v.duration) * 100)))
    return `Stopped at ${fmt(v.resume_position)} of ${fmt(v.duration)} (${pct}%) · ${at}`
  }
  return `Opened ${at}`
}
