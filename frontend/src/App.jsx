import React, { Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import useStore from "./store/useStore";

const AuthPage = React.lazy(() => import("./pages/AuthPage"));
const ProjectHub = React.lazy(() => import("./pages/ProjectHub"));
const SusceptibilityModule = React.lazy(() => import("./pages/SusceptibilityModule"));
const RainfallModule = React.lazy(() => import("./pages/RainfallModule"));
const AdminDashboard  = React.lazy(() => import("./pages/AdminDashboard"));
const DynamicModule   = React.lazy(() => import("./pages/DynamicModule"));

const Loader = () => (
  <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "#f8f8f9" }}>
    <div style={{ width: 32, height: 32, border: "2px solid #e5e5e7", borderTopColor: "#000", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
  </div>
);

const PrivateRoute = ({ children, adminOnly = false }) => {
  const { user, token } = useStore();
  if (!token || !user) return <Navigate to="/" replace />;
  if (adminOnly && user.role !== "admin") return <Navigate to="/hub" replace />;
  return children;
};

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<Loader />}>
        <Routes>
          <Route path="/" element={<AuthPage />} />
          <Route path="/hub" element={<PrivateRoute><ProjectHub /></PrivateRoute>} />
          <Route path="/susceptibility" element={<PrivateRoute><SusceptibilityModule /></PrivateRoute>} />
          <Route path="/rainfall" element={<PrivateRoute><RainfallModule /></PrivateRoute>} />
          <Route path="/dynamic" element={<PrivateRoute><DynamicModule /></PrivateRoute>} />
          <Route path="/admin" element={<PrivateRoute adminOnly><AdminDashboard /></PrivateRoute>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
