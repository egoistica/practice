import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

type TokenModalProps = {
  isOpen: boolean;
  isSubmitting: boolean;
  username?: string;
  onClose: () => void;
  onSubmit: (payload: { amount: number; reason: string }) => Promise<void>;
};

export default function TokenModal({ isOpen, isSubmitting, username, onClose, onSubmit }: TokenModalProps) {
  const [amount, setAmount] = useState(100);
  const [reason, setReason] = useState("admin adjustment");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const amountInputRef = useRef<HTMLInputElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setAmount(100);
    setReason("admin adjustment");
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
      amountInputRef.current?.focus();
    }, 0);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen]);

  const canSubmit = useMemo(() => amount > 0 && reason.trim().length >= 3 && !isSubmitting, [amount, isSubmitting, reason]);

  if (!isOpen) {
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    await onSubmit({ amount, reason: reason.trim() });
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
      aria-labelledby="token-modal-title"
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
          width: "min(460px, 92vw)",
          display: "grid",
          gap: "0.65rem",
        }}
      >
        <h3 id="token-modal-title" style={{ margin: 0 }}>
          Add Tokens {username ? `for ${username}` : ""}
        </h3>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Amount
          <input
            min={1}
            onChange={(event) => setAmount(Number(event.target.value))}
            ref={amountInputRef}
            required
            type="number"
            value={amount}
          />
        </label>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Reason
          <input onChange={(event) => setReason(event.target.value)} required type="text" value={reason} />
        </label>
        <div style={{ display: "flex", gap: "0.6rem", justifyContent: "flex-end" }}>
          <button onClick={onClose} type="button">
            Cancel
          </button>
          <button disabled={!canSubmit} type="submit">
            {isSubmitting ? "Saving..." : "Add tokens"}
          </button>
        </div>
      </form>
    </div>
  );
}
