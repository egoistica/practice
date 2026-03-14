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
  return "Registration failed. Please verify the form and try again.";
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const { register, isAuthenticated, isLoading } = useAuth();
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const canSubmit = useMemo(
    () =>
      !submitting &&
      username.trim().length > 0 &&
      email.trim().length > 0 &&
      password.trim().length > 0,
    [email, password, submitting, username],
  );

  if (!isLoading && isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await register({ username: username.trim(), email: email.trim(), password });
      navigate("/dashboard", { replace: true });
    } catch (submitError) {
      setError(extractErrorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ maxWidth: "28rem" }}>
      <h2>Register</h2>
      <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Username
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            required
            type="text"
            value={username}
          />
        </label>

        <label style={{ display: "grid", gap: "0.25rem" }}>
          Email
          <input
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
            required
            type="email"
            value={email}
          />
        </label>

        <label style={{ display: "grid", gap: "0.25rem" }}>
          Password
          <input
            autoComplete="new-password"
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
          {submitting ? "Creating account..." : "Create Account"}
        </button>
      </form>

      <p style={{ marginTop: "1rem" }}>
        Already registered? <Link to="/login">Sign in</Link>
      </p>
    </section>
  );
}

