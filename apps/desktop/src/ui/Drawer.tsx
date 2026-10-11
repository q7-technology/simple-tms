import { useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "./icons";

/** A screen over the map. Escape or the close button goes back to the map. */
export function Drawer({ label, wide, head, children }: {
  label: string; wide?: boolean; head: ReactNode; children: ReactNode;
}) {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") navigate("/"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
  return (
    <>
      <div className="dim" aria-hidden="true" onClick={() => navigate("/")} />
      <section className={`drawer${wide ? " wide" : ""}`} aria-label={label} role="dialog">
        <header className="dhead">
          <button type="button" className="closebtn" aria-label="Back to the map" onClick={() => navigate("/")}>
            <Icon name="close" size={20} width={2.4} />
          </button>
          {head}
        </header>
        <div className="dbody">{children}</div>
      </section>
    </>
  );
}
