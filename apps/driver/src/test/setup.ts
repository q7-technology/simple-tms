import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

// jsdom draws nothing: a canvas has no context, as on a phone that can't draw
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  window.localStorage.clear();
});
