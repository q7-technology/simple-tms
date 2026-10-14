/** Asking the API again every so often, only while someone can see the page:
 * a tab in the background asks nothing, and asks at once when it is back. */
import { useEffect, useRef } from "react";

export function usePoll(ask: () => void, everyMs: number) {
  const latest = useRef(ask);
  latest.current = ask;
  useEffect(() => {
    const t = setInterval(() => { if (!document.hidden) latest.current(); }, everyMs);
    const back = () => { if (!document.hidden) latest.current(); };
    document.addEventListener("visibilitychange", back);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", back); };
  }, [everyMs]);
}
