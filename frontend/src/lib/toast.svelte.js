// A one-line message that fades after a moment ("Added to queue"). One at a time.
export const toast = $state({ text: '' })
let timer
export function say(text, ms = 2400) {
  toast.text = text
  clearTimeout(timer)
  timer = setTimeout(() => (toast.text = ''), ms)
}
