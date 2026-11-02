// SponsorBlock helpers: colours and deciding which segments apply.

// The categories' usual colours, so the seek bar looks familiar.
export const COLORS = {
  sponsor: '#00d400', selfpromo: '#ffff00', interaction: '#cc00ff', intro: '#00ffff', outro: '#0202ed',
  preview: '#008fd6', filler: '#7300ff', music_offtopic: '#ff9900', exclusive_access: '#008a5c',
  poi_highlight: '#ff1684',
}

/** Per-category action for this channel: a channel override wins over the global setting. */
export function resolveActions(settings, channelId) {
  return { ...settings.categories, ...(settings.channels?.[channelId] ?? {}) }
}

/**
 * The segments that should do something on this video.
 * - ignored categories are dropped;
 * - segments shorter than the minimum are dropped (highlights are points, so they are kept);
 * - segments submitted for a different cut of the video (length differs by >3 s) are dropped,
 *   because their times would be wrong.
 */
export function usable(segments, settings, actions, videoDuration) {
  return segments.filter((s) => {
    if ((actions[s.category] ?? 'ignore') === 'ignore') return false
    if (s.action !== 'poi' && s.end - s.start < settings.min_length) return false
    if (s.video_duration && videoDuration && Math.abs(s.video_duration - videoDuration) > 3) return false
    return true
  })
}
