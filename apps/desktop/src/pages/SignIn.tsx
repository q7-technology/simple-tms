import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Logo } from "../ui/icons";

/** What a refusal means, in a sentence. The API says how many tries are left. */
function refusal(e: unknown): string {
  if (!(e instanceof ApiError)) return "Simple TMS could not be reached. Check the connection and try again.";
  if (e.code === "locked") return e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".";
  if (e.status === 401) {
    const left = (e.body as { tries_left?: number })?.tries_left;
    return left !== undefined
      ? `Wrong username or password. ${left} ${left === 1 ? "try" : "tries"} left before the account locks.`
      : "Wrong username or password.";
  }
  return e.message;
}

export function SignIn() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(refusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-3xl p-8 flex flex-col gap-4"
            style={{ boxShadow: "var(--shadow-lift)" }}>
        <div className="brand"><Logo /><span>Simple TMS</span></div>
        <h1 className="m-0 text-2xl font-extrabold">Sign in</h1>
        <label className="flex flex-col gap-1 text-sm font-bold text-ink-2">Username
          <input className="field" autoComplete="username" value={username}
                 onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="flex flex-col gap-1 text-sm font-bold text-ink-2">Password
          <input className="field" type="password" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="msg r" role="alert">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy}>Sign in</button>
      </form>
    </main>
  );
}
