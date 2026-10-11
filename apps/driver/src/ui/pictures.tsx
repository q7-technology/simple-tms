/** Taking a photo with the phone's camera, and signing on the screen. */
import { useEffect, useRef, useState, type RefObject } from "react";
import { shrinkPhoto } from "../lib/picture";
import { Icon } from "./icons";

/** "Add a photo": the phone's own camera through a file input, made small
 * on the phone before it is kept. */
export function PhotoPicker({ photo, onPhoto, hint }: { photo: Blob | null; onPhoto: (b: Blob | null) => void; hint: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!photo) { setUrl(null); return; }
    const u = URL.createObjectURL(photo);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [photo]);

  async function picked(file: File | undefined) {
    if (!file) return;
    setError(null);
    try {
      onPhoto(await shrinkPhoto(file));
    } catch {
      setError("That photo couldn't be read. Take it again.");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 8 }}>
        {url && <img src={url} alt="The photo taken" style={{ width: 76, height: 66, borderRadius: 12, objectFit: "cover", flex: "none" }} />}
        <label className="scanbox quiet">
          <Icon name="camera" size={26} />
          <span><b>{photo ? "Retake photo" : "Add a photo"}</b><small>{photo ? `${hint} · 1 added` : hint}</small></span>
          <input type="file" accept="image/*" capture="environment" className="sr"
                 onChange={(e) => { void picked(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
    </div>
  );
}

/** SIGN HERE: a canvas the customer signs with a finger. `signed` says
 * whether anything has been drawn. */
export function SignaturePad({ canvas, onSigned }: { canvas: RefObject<HTMLCanvasElement | null>; onSigned: (signed: boolean) => void }) {
  const drawing = useRef(false);
  // what a screen reader hears: a drawing can't be read out, so say what it's for and whether it holds one
  const [hasInk, setHasInk] = useState(false);

  function context(): CanvasRenderingContext2D | null {
    return canvas.current?.getContext("2d") ?? null;
  }

  function clear() {
    const el = canvas.current;
    const ctx = context();
    if (el && ctx) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, el.width, el.height);
    }
    setHasInk(false);
    onSigned(false);
  }

  useEffect(() => {
    // as many pixels as the screen has, so the signature isn't blurred
    const el = canvas.current;
    if (!el) return;
    const ratio = window.devicePixelRatio || 1;
    el.width = Math.round(el.clientWidth * ratio) || 600;
    el.height = Math.round(el.clientHeight * ratio) || 300;
    const ctx = context();
    if (ctx) {
      ctx.scale(ratio, ratio);
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = "#17251F";
    }
    clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function at(e: React.PointerEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  }

  return (
    <section aria-label="Signature" style={{ borderRadius: 18, background: "#fff", border: "2px solid var(--color-line-2)",
                                             padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="lbl">Sign here</span>
        <button type="button" onClick={clear} aria-label="Clear the signature"
                style={{ border: 0, background: "transparent", color: "var(--color-green-dark)", fontWeight: 800, fontSize: 13, cursor: "pointer" }}>
          Clear</button>
      </div>
      <canvas ref={canvas} className="pad" role="img" aria-describedby="sign-how"
              aria-label={hasInk ? "Signature pad: signed" : "Signature pad: empty"}
              onPointerDown={(e) => {
                const ctx = context();
                if (!ctx) return;
                e.currentTarget.setPointerCapture?.(e.pointerId);
                drawing.current = true;
                const [x, y] = at(e);
                ctx.beginPath();
                ctx.moveTo(x, y);
              }}
              onPointerMove={(e) => {
                const ctx = context();
                if (!drawing.current || !ctx) return;
                const [x, y] = at(e);
                ctx.lineTo(x, y);
                ctx.stroke();
                setHasInk(true);
                onSigned(true);
              }}
              onPointerUp={() => { drawing.current = false; }}
              onPointerCancel={() => { drawing.current = false; }} />
      <span id="sign-how" className="sr">The customer signs with a finger (or the mouse). Can't sign here? Take a photo instead.</span>
    </section>
  );
}
