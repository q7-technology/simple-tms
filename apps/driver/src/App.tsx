import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./auth/Session";
import { CantDeliver } from "./pages/CantDeliver";
import { OnTheRun } from "./pages/OnTheRun";
import { Proof } from "./pages/Proof";
import { RunHome } from "./pages/RunHome";
import { SignIn } from "./pages/SignIn";
import { Stop } from "./pages/Stop";
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
        <Route path="/run/:runId" element={<OnTheRun />}>
          <Route index element={<RunHome />} />
          <Route path="stop/:consignmentId" element={<Stop />} />
          <Route path="stop/:consignmentId/proof" element={<Proof />} />
          <Route path="stop/:consignmentId/cant" element={<CantDeliver />} />
        </Route>
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
