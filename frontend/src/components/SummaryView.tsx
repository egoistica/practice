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
  summary: SummaryData;
};

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

export default function SummaryView({ summary }: SummaryViewProps) {
  return (
    <section style={{ display: "grid", gap: "0.75rem", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>Summary</h3>
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
          {summary.enriched ? "✓ Enriched" : "Not enriched"}
        </span>
      </div>

      {summary.blocks.length === 0 ? <p>No summary blocks yet.</p> : null}

      <div style={{ display: "grid", gap: "0.6rem" }}>
        {summary.blocks.map((block, index) => {
          const startLabel = formatTimecode(block.timecode_start);
          const endLabel = formatTimecode(block.timecode_end);
          const hasTimecode = Boolean(startLabel || endLabel);
          return (
            <article
              key={`${summary.id}-${index}-${block.title}`}
              style={{
                border: "1px solid #d6d6d6",
                borderRadius: "0.6rem",
                padding: "0.75rem",
                display: "grid",
                gap: "0.4rem",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
                <strong>{block.title}</strong>
                <span
                  style={{
                    fontSize: "0.8rem",
                    color: "#1f2937",
                    background: "#eef2ff",
                    borderRadius: "999px",
                    padding: "0.2rem 0.55rem",
                  }}
                >
                  {blockTypeLabel(block.type)}
                </span>
              </div>
              {hasTimecode ? (
                <small style={{ color: "#4b5563" }}>
                  {startLabel || "?"} - {endLabel || "?"}
                </small>
              ) : null}
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{block.text}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
