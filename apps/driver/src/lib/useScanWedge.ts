import { useEffect, useRef } from "react";

/** Keyboard-wedge scanners type the code fast and press Enter. Collect a
 * burst of keys with short gaps; deliver it on Enter. Human typing into a
 * normal input is left alone; an input with data-scan="true" is treated as
 * a scan field so its Enter also delivers. (As the Simple WMS scanner's.) */
export function useScanWedge(onScan: (code: string) => void, opts: { maxGapMs?: number; minLength?: number } = {}) {
  const handler = useRef(onScan);
  handler.current = onScan;
  const maxGap = opts.maxGapMs ?? 80;
  const minLength = opts.minLength ?? 1;

  useEffect(() => {
    let buffer = "";
    let last = 0;
    let fast = true;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      const scanField = inField && target!.dataset.scan === "true";
      if (inField && !scanField) return;
      const now = Date.now();
      if (e.key === "Enter") {
        const code = scanField ? (target as HTMLInputElement).value.trim() || buffer : buffer;
        if ((scanField || fast) && code.length >= minLength) {
          e.preventDefault();
          handler.current(code);
          if (scanField) (target as HTMLInputElement).value = "";
        }
        buffer = "";
        fast = true;
        return;
      }
      if (e.key.length !== 1) return;
      if (buffer && now - last > maxGap) {
        // slow typing: start over and remember it was not a scanner
        buffer = "";
        fast = false;
      } else if (!buffer) {
        fast = true;
      }
      buffer += e.key;
      last = now;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maxGap, minLength]);
}
