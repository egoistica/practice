import { FormEvent, useEffect, useMemo, useState } from "react";

type CreateUserPayload = {
  username: string;
  email: string;
  password?: string;
  generate_password: boolean;
  is_admin: boolean;
  is_active: boolean;
};

type CreateUserModalProps = {
  isOpen: boolean;
  isSubmitting: boolean;
  onClose: () => void;
  onSubmit: (payload: CreateUserPayload) => Promise<void>;
};

export default function CreateUserModal({ isOpen, isSubmitting, onClose, onSubmit }: CreateUserModalProps) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [generatePassword, setGeneratePassword] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setUsername("");
    setEmail("");
    setPassword("");
    setGeneratePassword(false);
    setIsAdmin(false);
    setIsActive(true);
  }, [isOpen]);

  const canSubmit = useMemo(() => {
    if (!username.trim() || !email.trim() || isSubmitting) {
      return false;
    }
    if (!generatePassword && password.trim().length < 8) {
      return false;
    }
    return true;
  }, [email, generatePassword, isSubmitting, password, username]);

  if (!isOpen) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    await onSubmit({
      username: username.trim(),
      email: email.trim(),
      password: generatePassword ? undefined : password,
      generate_password: generatePassword,
      is_admin: isAdmin,
      is_active: isActive,
    });
  }

  return (
    <div
      aria-modal="true"
      role="dialog"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.35)",
        display: "grid",
        placeItems: "center",
        zIndex: 1000,
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          background: "#fff",
          borderRadius: "0.75rem",
          padding: "1rem",
          width: "min(560px, 92vw)",
          display: "grid",
          gap: "0.65rem",
        }}
      >
        <h3 style={{ margin: 0 }}>Create User</h3>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Username
          <input onChange={(event) => setUsername(event.target.value)} required type="text" value={username} />
        </label>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Email
          <input onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
          <input
            checked={generatePassword}
            onChange={(event) => setGeneratePassword(event.target.checked)}
            type="checkbox"
          />
          Generate password automatically
        </label>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Password
          <input
            disabled={generatePassword}
            minLength={8}
            onChange={(event) => setPassword(event.target.value)}
            required={!generatePassword}
            type="password"
            value={password}
          />
        </label>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
            <input checked={isAdmin} onChange={(event) => setIsAdmin(event.target.checked)} type="checkbox" />
            Admin
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
            <input checked={isActive} onChange={(event) => setIsActive(event.target.checked)} type="checkbox" />
            Active
          </label>
        </div>
        <div style={{ display: "flex", gap: "0.6rem", justifyContent: "flex-end" }}>
          <button onClick={onClose} type="button">
            Cancel
          </button>
          <button disabled={!canSubmit} type="submit">
            {isSubmitting ? "Creating..." : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
