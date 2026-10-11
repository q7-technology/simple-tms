import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./auth/Session";
import { RunHome } from "./pages/RunHome";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";

function RequireDriver() {
  const { driver } = useSession();
  return driver ? <Outlet /> : <Navigate to="/sign-in" replace />;
}

/** One thing at a time: today's run, then each stop in turn. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireDriver />}>
        <Route index element={<Today />} />
        <Route path="/run/:runId" element={<RunHome />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter basename="/driver">
      <SessionProvider>
        <AppRoutes />
      </SessionProvider>
    </BrowserRouter>
  );
}
