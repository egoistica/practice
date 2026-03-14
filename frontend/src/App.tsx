import { Suspense, lazy } from "react";
import { Link, Navigate, Outlet, Route, Routes } from "react-router-dom";

import { useAuth } from "./hooks/useAuth";
import DashboardPage from "./pages/Dashboard";
import AdminDashboardPage from "./pages/admin/AdminDashboard";
import AdminDatabaseStatsPage from "./pages/admin/AdminDatabaseStats";
import AdminStatisticsPage from "./pages/admin/AdminStatistics";
import UsersPage from "./pages/admin/UsersPage";
import FavouritesPage from "./pages/Favourites";
import HistoryPage from "./pages/History";
import LoginPage from "./pages/Login";
import RegisterPage from "./pages/Register";
import UploadPage from "./pages/Upload";

const LecturePage = lazy(() => import("./pages/Lecture"));

function HomePage() {
  return (
    <section>
      <h2>Home</h2>
      <p>Frontend is ready for API integration.</p>
    </section>
  );
}

function ProfilePage() {
  const { user, isLoading } = useAuth();
  if (isLoading) {
    return (
      <section>
        <h2>Profile</h2>
        <p>Loading profile...</p>
      </section>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return (
    <section>
      <h2>Profile</h2>
      <p>Username: {user.username}</p>
      <p>Email: {user.email}</p>
      <p>Admin: {String(user.is_admin)}</p>
    </section>
  );
}

function DefaultRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) {
    return <p>Loading...</p>;
  }
  return <Navigate to={isAuthenticated ? "/dashboard" : "/login"} replace />;
}

function AdminRoute() {
  const { user, isLoading } = useAuth();
  if (isLoading) {
    return <p>Loading...</p>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if (!user.is_admin) {
    return <Navigate to="/dashboard" replace />;
  }
  return <Outlet />;
}

export default function App() {
  const { user, isAuthenticated, isLoading, logout } = useAuth();
  const isAdmin = Boolean(user?.is_admin);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", margin: "2rem" }}>
      <h1>Lecture Notes Frontend</h1>
      <p>Status: {isLoading ? "loading..." : isAuthenticated ? "authenticated" : "guest"}</p>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
        <Link to="/home">Home</Link>
        <Link to="/dashboard">Dashboard</Link>
        {isAuthenticated ? <Link to="/favourites">Favourites</Link> : null}
        {isAuthenticated ? <Link to="/history">History</Link> : null}
        {isAdmin ? <Link to="/admin">Admin</Link> : null}
        <Link to="/profile">Profile</Link>
        {!isAuthenticated ? <Link to="/login">Login</Link> : null}
        {!isAuthenticated ? <Link to="/register">Register</Link> : null}
        {isAuthenticated ? (
          <button onClick={logout} type="button">
            Logout
          </button>
        ) : null}
      </nav>

      <Routes>
        <Route path="/" element={<DefaultRoute />} />
        <Route path="/home" element={<HomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/favourites" element={<FavouritesPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route element={<AdminRoute />} path="/admin">
          <Route element={<AdminDashboardPage />} index />
          <Route element={<UsersPage />} path="users" />
          <Route element={<AdminStatisticsPage />} path="statistics" />
          <Route element={<AdminDatabaseStatsPage />} path="database-stats" />
        </Route>
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route
          path="/lecture/:lectureId"
          element={
            <Suspense fallback={<p>Loading lecture page...</p>}>
              <LecturePage />
            </Suspense>
          }
        />
      </Routes>
    </main>
  );
}
