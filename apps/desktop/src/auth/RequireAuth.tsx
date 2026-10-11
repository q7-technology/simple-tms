import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function RequireAuth() {
  const { ready, user, trouble, retry } = useAuth();
  if (!ready) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!user && trouble) {
    // the session is kept: sending them to sign in would throw it away for nothing
    return (
      <main className="min-h-screen grid place-items-center p-6">
        <div className="w-full max-w-sm bg-white rounded-3xl p-8 flex flex-col gap-4" style={{ boxShadow: "var(--shadow-lift)" }}>
          <div className="msg r" role="alert">{trouble}</div>
          <button type="button" className="btn primary" onClick={retry}>Try again</button>
        </div>
      </main>
    );
  }
  if (!user) return <Navigate to="/sign-in" replace />;
  return <Outlet />;
}
