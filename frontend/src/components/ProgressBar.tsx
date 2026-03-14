import { useEffect, useMemo, useRef, useState } from "react";

type ProgressUpdate = {
  progress: number;
  status: string;
};

type ProgressBarProps = {
  lectureId: string;
  token: string | null;
  initialProgress: number;
  initialStatus: string;
  onProgress?: (update: ProgressUpdate) => void;
};

const TERMINAL_STATUSES = new Set(["done", "error"]);

function normalizeProgress(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function normalizeStatus(value: unknown, fallback: string): string {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  return value.trim().toLowerCase();
}

function buildWebSocketUrl(lectureId: string, token: string): string {
  const rawBase = import.meta.env.VITE_API_BASE_URL || window.location.origin;
  let parsed: URL;
  try {
    parsed = new URL(rawBase);
  } catch {
    parsed = new URL(window.location.origin);
  }
  const protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${parsed.host}/ws/${encodeURIComponent(lectureId)}?token=${encodeURIComponent(token)}`;
}

export default function ProgressBar({
  lectureId,
  token,
  initialProgress,
  initialStatus,
  onProgress,
}: ProgressBarProps) {
  const normalizedInitial = useMemo<ProgressUpdate>(
    () => ({
      progress: normalizeProgress(initialProgress),
      status: normalizeStatus(initialStatus, "pending"),
    }),
    [initialProgress, initialStatus],
  );
  const [state, setState] = useState<ProgressUpdate>(normalizedInitial);
  const stateRef = useRef<ProgressUpdate>(normalizedInitial);

  useEffect(() => {
    const next = {
      progress: normalizeProgress(initialProgress),
      status: normalizeStatus(initialStatus, "pending"),
    };
    setState(next);
    stateRef.current = next;
  }, [initialProgress, initialStatus]);

  useEffect(() => {
    if (!token) {
      return;
    }

    let isStopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (isStopped || TERMINAL_STATUSES.has(stateRef.current.status)) {
        return;
      }

      socket = new WebSocket(buildWebSocketUrl(lectureId, token));

      socket.onmessage = (event) => {
        let payload: unknown;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        if (!payload || typeof payload !== "object") {
          return;
        }

        const mapped = payload as Record<string, unknown>;
        const incomingLectureId = String(mapped.lecture_id ?? "");
        if (incomingLectureId !== lectureId) {
          return;
        }
        if (mapped.type !== "lecture_progress") {
          return;
        }

        const next: ProgressUpdate = {
          progress: normalizeProgress(Number(mapped.progress ?? 0)),
          status: normalizeStatus(mapped.status, stateRef.current.status),
        };
        stateRef.current = next;
        setState(next);
      };

      socket.onclose = () => {
        if (isStopped || TERMINAL_STATUSES.has(stateRef.current.status)) {
          return;
        }
        reconnectTimer = window.setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      isStopped = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      if (socket) {
        socket.close();
      }
    };
  }, [lectureId, token]);

  useEffect(() => {
    if (onProgress) {
      onProgress(state);
    }
  }, [onProgress, state]);

  return (
    <section style={{ display: "grid", gap: "0.4rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <strong>Processing progress</strong>
        <span>
          {state.progress}% ({state.status})
        </span>
      </div>
      <div
        aria-label="Lecture processing progress"
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={state.progress}
        role="progressbar"
        style={{
          width: "100%",
          height: "0.9rem",
          background: "#e5e7eb",
          borderRadius: "0.6rem",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${state.progress}%`,
            height: "100%",
            background: state.status === "error" ? "#dc2626" : "#2563eb",
            transition: "width 0.25s ease",
          }}
        />
      </div>
    </section>
  );
}
