import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { MapHome } from "./pages/MapHome";
import { PlanRuns } from "./pages/PlanRuns";
import { Later } from "./pages/Later";
import { SignIn } from "./pages/SignIn";

/** The map is home; every other screen opens as a drawer over it. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireAuth />}>
        <Route element={<MapHome />}>
          <Route index element={null} />
          <Route path="/plan" element={<PlanRuns />} />
          <Route path="/consignments" element={
            <Later title="Consignments" step="a later build step"
                   what="Search by WMS delivery, customer or con note, and see each one's history." />} />
          <Route path="/carriers" element={
            <Later title="Carriers" step="build step 6"
                   what="Today's carrier consignments, con note entry and the manifest." />} />
          <Route path="/fleet" element={
            <Later title="Fleet" step="build step 4"
                   what="Vehicles, drivers, pre-start checks, and rego and service due." />} />
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
