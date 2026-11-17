// What kind of screen this is right now. `phoneLandscape`: a touch screen held sideways (short and wide), where the watch page
// defaults to the theater view: the video fills the screen and the details wait below it.
const query = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(orientation: landscape) and (max-height: 520px) and (pointer: coarse)') : null

export const view = $state({ phoneLandscape: !!query?.matches })

query?.addEventListener?.('change', (e) => { view.phoneLandscape = e.matches })
