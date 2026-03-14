import { FormEvent, useEffect, useMemo, useState } from "react";

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

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    setAmount(100);
    setReason("admin adjustment");
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
          width: "min(460px, 92vw)",
          display: "grid",
          gap: "0.65rem",
        }}
      >
        <h3 style={{ margin: 0 }}>Add Tokens {username ? `for ${username}` : ""}</h3>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Amount
          <input
            min={1}
            onChange={(event) => setAmount(Number(event.target.value))}
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
