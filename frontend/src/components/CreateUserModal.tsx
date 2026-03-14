import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

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
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const nameInputRef = useRef<HTMLInputElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

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

  useEffect(() => {
    if (!isOpen) {
      if (restoreFocusRef.current) {
        restoreFocusRef.current.focus();
      }
      return;
    }

    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusTimer = window.setTimeout(() => {
      nameInputRef.current?.focus();
    }, 0);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
    };
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

  function getFocusableElements(): HTMLElement[] {
    if (!dialogRef.current) {
      return [];
    }
    const elements = dialogRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    );
    return Array.from(elements).filter((element) => !element.hasAttribute("disabled"));
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !isSubmitting) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const focusableElements = getFocusableElements();
    if (focusableElements.length === 0) {
      event.preventDefault();
      return;
    }

    const first = focusableElements[0];
    const last = focusableElements[focusableElements.length - 1];
    const active = document.activeElement as HTMLElement | null;

    if (event.shiftKey) {
      if (!active || active === first || !focusableElements.includes(active)) {
        event.preventDefault();
        last.focus();
      }
      return;
    }

    if (!active || active === last || !focusableElements.includes(active)) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      aria-labelledby="create-user-title"
      aria-modal="true"
      onKeyDown={handleDialogKeyDown}
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
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
        <h3 id="create-user-title" style={{ margin: 0 }}>
          Create User
        </h3>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Username
          <input
            onChange={(event) => setUsername(event.target.value)}
            ref={nameInputRef}
            required
            type="text"
            value={username}
          />
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
