import { Link, Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./hooks/useAuth";

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
    return <Navigate to="/" replace />;
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

export default function App() {
  const { isAuthenticated, isLoading } = useAuth();

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", margin: "2rem" }}>
      <h1>Lecture Notes Frontend</h1>
      <p>Status: {isLoading ? "loading..." : isAuthenticated ? "authenticated" : "guest"}</p>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "1rem" }}>
        <Link to="/">Home</Link>
        <Link to="/profile">Profile</Link>
      </nav>

      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/profile" element={<ProfilePage />} />
      </Routes>
    </main>
  );
}
