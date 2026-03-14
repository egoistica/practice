import axios from "axios";
import { FormEvent, useMemo, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && typeof first.msg === "string") {
        return first.msg;
      }
    }
  }
  return "Login failed. Please check your credentials and try again.";
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { login, isAuthenticated, isLoading } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = useMemo(
    () => !isLoading && !submitting && username.trim().length > 0 && password.trim().length > 0,
    [isLoading, password, submitting, username],
  );

  if (isLoading) {
    return <p>Loading...</p>;
  }

  if (!isLoading && isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading || !canSubmit) {
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await login({ username: username.trim(), password });
      navigate("/dashboard", { replace: true });
    } catch (submitError) {
      setError(extractErrorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ maxWidth: "28rem" }}>
      <h2>Login</h2>
      <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Username or Email
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            required
            type="text"
            value={username}
          />
        </label>

        <label style={{ display: "grid", gap: "0.25rem" }}>
          Password
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>

        {error ? (
          <p style={{ color: "#b00020", margin: 0 }} role="alert">
            {error}
          </p>
        ) : null}

        <button disabled={!canSubmit} type="submit">
          {submitting ? "Signing in..." : "Sign In"}
        </button>
      </form>

      <p style={{ marginTop: "1rem" }}>
        No account yet? <Link to="/register">Create one</Link>
      </p>
    </section>
  );
}
