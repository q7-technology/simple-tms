import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { Carriers } from "./pages/Carriers";
import { Consignments } from "./pages/Consignments";
import { Fleet } from "./pages/Fleet";
import { MapHome } from "./pages/MapHome";
import { PlanRuns } from "./pages/PlanRuns";
import { Settings } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { WhereIsIt } from "./pages/WhereIsIt";

/** The map is home; every other screen opens as a drawer over it. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireAuth />}>
        <Route element={<MapHome />}>
          <Route index element={null} />
          <Route path="/plan" element={<PlanRuns />} />
          <Route path="/consignments" element={<Consignments />} />
          <Route path="/carriers" element={<Carriers />} />
          <Route path="/fleet" element={<Fleet />} />
          <Route path="/where" element={<WhereIsIt />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
