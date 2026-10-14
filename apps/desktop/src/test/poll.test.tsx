import { act, renderHook } from "@testing-library/react";
import { usePoll } from "../lib/poll";

function hidden(yes: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => yes });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("polling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); hidden(false); });

  it("asks every so often while the page is seen, not while it is hidden, and at once when it is back", () => {
    const ask = vi.fn();
    const { unmount } = renderHook(() => usePoll(ask, 30_000));
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(ask).toHaveBeenCalledTimes(1);
    act(() => hidden(true));
    act(() => { vi.advanceTimersByTime(90_000); });
    expect(ask).toHaveBeenCalledTimes(1);
    act(() => hidden(false));
    expect(ask).toHaveBeenCalledTimes(2);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(ask).toHaveBeenCalledTimes(3);
    unmount();
    act(() => { vi.advanceTimersByTime(60_000); hidden(true); hidden(false); });
    expect(ask).toHaveBeenCalledTimes(3);
  });
});
