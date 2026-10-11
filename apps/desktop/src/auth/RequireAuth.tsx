import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function RequireAuth() {
  const { ready, user } = useAuth();
  if (!ready) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!user) return <Navigate to="/sign-in" replace />;
  return <Outlet />;
}
