import axios from "axios";
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../api/client";

type SummaryBlock = {
  title: string;
  text: string;
  type: string;
  timecode_start: number | null;
  timecode_end: number | null;
};

type SummaryData = {
  id: string;
  blocks: SummaryBlock[];
  enriched: boolean;
};

type SummaryViewProps = {
  lectureId: string;
  summary: SummaryData;
  entityLabels?: string[];
  onEntityClick?: (entityLabel: string) => void;
  onTimecodeClick?: (seconds: number) => void;
};

function blockKey(block: SummaryBlock): string {
  return [
    block.title.trim().toLowerCase(),
    block.text.trim().toLowerCase(),
    block.type.trim().toLowerCase(),
    String(block.timecode_start ?? ""),
    String(block.timecode_end ?? ""),
  ].join("|");
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}

function formatTimecode(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return "";
  }
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function blockTypeLabel(type: string): string {
  const normalized = type.trim().toLowerCase();
  if (normalized === "definition") {
    return "Definition";
  }
  if (normalized === "date") {
    return "Date";
  }
  if (normalized === "conclusion") {
    return "Conclusion";
  }
  return type.trim() || type;
}

function blockTypeStyle(type: string): { borderColor: string; background: string; labelBg: string } {
  const normalized = type.trim().toLowerCase();
  if (normalized === "definition") {
    return { borderColor: "#2563eb", background: "#eff6ff", labelBg: "#dbeafe" };
  }
  if (normalized === "date") {
    return { borderColor: "#7c3aed", background: "#f5f3ff", labelBg: "#ede9fe" };
  }
  if (normalized === "conclusion") {
    return { borderColor: "#16a34a", background: "#f0fdf4", labelBg: "#dcfce7" };
  }
  return { borderColor: "#f59e0b", background: "#fffbeb", labelBg: "#fef3c7" };
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export default function SummaryView({
  lectureId,
  summary,
  entityLabels = [],
  onEntityClick,
  onTimecodeClick,
}: SummaryViewProps) {
  const queryClient = useQueryClient();
  const [enrichedKeys, setEnrichedKeys] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const normalizedEntities = useMemo(() => {
    const seen = new Set<string>();
    const labels = entityLabels
      .map((label) => label.trim())
      .filter((label) => label.length > 1)
      .sort((a, b) => b.length - a.length)
      .filter((label) => {
        const key = label.toLowerCase();
        if (seen.has(key)) {
          return false;
        }
        seen.add(key);
        return true;
      });
    return labels.slice(0, 150);
  }, [entityLabels]);

  const enrichMutation = useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<SummaryData>(`/lectures/${lectureId}/summary/enrich`, {});
      return response.data;
    },
    onSuccess: (updatedSummary) => {
      const previousBlocks = summary.blocks;
      const previousKeys = new Set(previousBlocks.map(blockKey));
      const nextEnrichedKeys = new Set<string>();
      for (const block of updatedSummary.blocks) {
        const key = blockKey(block);
        if (!previousKeys.has(key)) {
          nextEnrichedKeys.add(key);
        }
      }
      setEnrichedKeys(nextEnrichedKeys);
      setActionError(null);
      queryClient.setQueryData(["lecture-summary", lectureId], updatedSummary);
    },
    onError: (error) => {
      setActionError(extractErrorMessage(error, "Failed to enrich summary."));
    },
  });

  function renderTextWithEntities(text: string) {
    if (!normalizedEntities.length || !onEntityClick) {
      return <>{text}</>;
    }
    const pattern = new RegExp(`(${normalizedEntities.map(escapeRegex).join("|")})`, "gi");
    const pieces = text.split(pattern);
    return (
      <>
        {pieces.map((piece, index) => {
          const matchedLabel = normalizedEntities.find((label) => label.toLowerCase() === piece.toLowerCase());
          if (!matchedLabel) {
            return <span key={`text-${index}`}>{piece}</span>;
          }
          return (
            <button
              key={`entity-${index}-${matchedLabel}`}
              onClick={() => onEntityClick(matchedLabel)}
              style={{
                border: "1px solid #93c5fd",
                borderRadius: "0.35rem",
                background: "#eff6ff",
                color: "#1d4ed8",
                cursor: "pointer",
                padding: "0 0.25rem",
                margin: "0 0.1rem",
              }}
              type="button"
            >
              {piece}
            </button>
          );
        })}
      </>
    );
  }

  return (
    <section style={{ display: "grid", gap: "0.75rem", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>Summary</h3>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span
            style={{
              fontSize: "0.9rem",
              color: summary.enriched ? "#166534" : "#374151",
              background: summary.enriched ? "#dcfce7" : "#f3f4f6",
              borderRadius: "999px",
              padding: "0.2rem 0.6rem",
            }}
            title={summary.enriched ? "Enrichment enabled for this summary" : "No enrichment yet"}
          >
            {summary.enriched ? "Enriched" : "Not enriched"}
          </span>
          <button disabled={enrichMutation.isPending} onClick={() => enrichMutation.mutate()} type="button">
            {enrichMutation.isPending ? "Enriching..." : "Enrich summary"}
          </button>
        </div>
      </div>

      {actionError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {actionError}
        </p>
      ) : null}

      {summary.blocks.length === 0 ? <p>No summary blocks yet.</p> : null}

      <div style={{ display: "grid", gap: "0.6rem" }}>
        {summary.blocks.map((block, index) => {
          const style = blockTypeStyle(block.type);
          const key = blockKey(block);
          const isEnrichedBlock = enrichedKeys.has(key);
          const startLabel = formatTimecode(block.timecode_start);
          const endLabel = formatTimecode(block.timecode_end);
          const hasTimecode = Boolean(startLabel || endLabel);
          const linkTimecode = block.timecode_start ?? block.timecode_end;
          return (
            <article
              key={`${summary.id}-${index}-${block.title}`}
              style={{
                border: `1px ${isEnrichedBlock ? "dashed" : "solid"} ${style.borderColor}`,
                borderRadius: "0.6rem",
                padding: "0.75rem",
                display: "grid",
                gap: "0.4rem",
                background: style.background,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
                <strong>{block.title}</strong>
                <span
                  style={{
                    fontSize: "0.8rem",
                    color: "#1f2937",
                    background: style.labelBg,
                    borderRadius: "999px",
                    padding: "0.2rem 0.55rem",
                  }}
                >
                  {blockTypeLabel(block.type)}
                </span>
              </div>
              {hasTimecode ? (
                <small style={{ color: "#4b5563" }}>
                  {startLabel || "?"} - {endLabel || "?"}{" "}
                  {linkTimecode !== null ? (
                    <a
                      href={`/lecture/${encodeURIComponent(lectureId)}?t=${Math.floor(linkTimecode)}`}
                      onClick={(event) => {
                        if (!onTimecodeClick) {
                          return;
                        }
                        event.preventDefault();
                        onTimecodeClick(Math.floor(linkTimecode));
                      }}
                    >
                      Open timecode
                    </a>
                  ) : null}
                </small>
              ) : null}
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{renderTextWithEntities(block.text)}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
