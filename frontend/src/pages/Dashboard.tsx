import { Navigate } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";

export default function DashboardPage() {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <section>
        <h2>Dashboard</h2>
        <p>Loading...</p>
      </section>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <section>
      <h2>Dashboard</h2>
      <p>Welcome, {user.username}.</p>
      <p>Your account is active: {String(user.is_active)}</p>
    </section>
  );
}

