import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { SIGN_IN_NOT_KEPT, useSession } from "../auth/Session";
import { Icon, Logo } from "../ui/icons";
import { Phone } from "../ui/kit";

const MAX_PIN = 8;

function refusal(e: unknown): string {
  if (e instanceof Error && e.message === SIGN_IN_NOT_KEPT) return e.message;
  if (!(e instanceof ApiError)) return "No signal: signing in needs a connection. Try again in range.";
  if (e.code === "locked") return e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".";
  if (e.code === "wrong_pin") return "Wrong driver code or PIN.";
  return e.message;
}

/** DriverSignIn.html: driver code and PIN, on a keypad. */
export function SignIn() {
  const { driver, signIn } = useSession();
  const [code, setCode] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // a phone with a keyboard can type the PIN too
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (/^\d$/.test(e.key)) setPin((p) => (p.length < MAX_PIN ? p + e.key : p));
      if (e.key === "Backspace") setPin((p) => p.slice(0, -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (driver) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(code.trim(), pin);
    } catch (err) {
      setError(refusal(err));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  const dots = Math.max(4, pin.length);
  const press = (d: string) => setPin((p) => (p.length < MAX_PIN ? p + d : p));
  return (
    <Phone label="Driver sign-in">
      <form onSubmit={submit} style={{ display: "contents" }}>
        <div className="top" style={{ justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}><Logo />
            <span style={{ fontSize: 17, fontWeight: 800 }}>Simple TMS</span></div>
          <span className="chip">Driver app</span>
        </div>
        <h1 style={{ margin: "4px 0 0", fontSize: 28, fontWeight: 800, letterSpacing: -0.4 }}>Morning! Ready to roll?</h1>
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>Driver code
          <input className="input" type="text" autoComplete="username" autoCapitalize="characters" autoCorrect="off"
                 spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>PIN</span>
          <div role="img" aria-label={`PIN, ${pin.length} ${pin.length === 1 ? "number" : "numbers"} entered`}
               style={{ display: "flex", justifyContent: "center", gap: 18, padding: "2px 0" }}>
            {Array.from({ length: dots }, (_, i) => (
              <span key={i} style={{ width: 18, height: 18, borderRadius: "50%", border: "2px solid var(--color-green)",
                                     background: i < pin.length ? "var(--color-green)" : "transparent" }} />
            ))}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <button key={d} type="button" className="key" onClick={() => press(d)}>{d}</button>
          ))}
          <button type="button" className="key quiet" aria-label="Clear PIN" onClick={() => setPin("")}>Clear</button>
          <button type="button" className="key" onClick={() => press("0")}>0</button>
          <button type="button" className="key quiet" aria-label="Delete last number" onClick={() => setPin((p) => p.slice(0, -1))}>
            <Icon name="delete" size={24} width={2.2} /></button>
        </div>
        {error && <div className="msg r" role="alert">{error}</div>}
        <button type="submit" className="big primary" disabled={busy || !code.trim() || pin.length < 4}>Sign in</button>
        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 6, alignItems: "center", textAlign: "center",
                      fontSize: 12, color: "var(--color-muted)" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, color: "var(--color-ink-2)" }}>
            <Icon name="pin" size={14} width={2.2} />Location is used only while you're on a run</span>
          <span>Your whole run loads onto the phone when you start, so black spots don't stop you.</span>
        </div>
      </form>
    </Phone>
  );
}
