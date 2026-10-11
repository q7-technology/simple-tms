import { useRef, useState, type ReactNode } from "react";

/** A menu that does something when an option is chosen ("Put on a run…").
 *
 * Chosen with the mouse, or from the browser's open list, it acts at once.
 * But on a closed menu an arrow key (Windows, Linux) or type-ahead (every
 * browser) changes the value straight away, so looking through the options
 * from the keyboard would move a consignment with the first key press. A
 * change made by a key only picks the option; Enter does it, and leaving the
 * menu or Escape forgets it. */
export function ActionSelect({ label, placeholder, disabled, onChoose, children }: {
  label: string; placeholder: string; disabled?: boolean;
  onChoose: (value: string) => void; children: ReactNode;
}) {
  const [picked, setPicked] = useState("");
  // set by a key press, cleared as soon as that key's own change has fired
  const keyed = useRef(false);

  function choose(value: string) {
    setPicked("");
    if (value) onChoose(value);
  }

  return (
    <select aria-label={label} value={picked} disabled={disabled}
            onKeyDown={(e) => {
              if (e.key === "Enter" && picked) { e.preventDefault(); choose(picked); return; }
              if (e.key === "Escape" && picked) { e.stopPropagation(); setPicked(""); return; }
              keyed.current = true;
              setTimeout(() => { keyed.current = false; }, 0);
            }}
            onBlur={() => setPicked("")}
            onChange={(e) => {
              if (keyed.current) setPicked(e.target.value);
              else choose(e.target.value);
            }}>
      <option value="">{placeholder}</option>
      {children}
    </select>
  );
}
