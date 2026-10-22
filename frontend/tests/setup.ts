import '@testing-library/jest-dom/vitest'

// jsdom does not implement media playback; the camera tests provide their own
// fake stream, so the method only has to exist and stay silent.
if (!HTMLMediaElement.prototype.play) {
  HTMLMediaElement.prototype.play = () => Promise.resolve()
}
