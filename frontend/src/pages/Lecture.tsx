import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useParams } from "react-router-dom";

import EntityGraph from "../components/EntityGraph";
import ProgressBar from "../components/ProgressBar";
import SummaryView from "../components/SummaryView";
import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

type LectureResponse = {
  id: string;
  title: string;
  status: string;
  processing_progress: number;
  created_at: string;
};

type SummaryBlock = {
  title: string;
  text: string;
  type: string;
  timecode_start: number | null;
  timecode_end: number | null;
};

type SummaryResponse = {
  id: string;
  blocks: SummaryBlock[];
  enriched: boolean;
};

type GraphMention = {
  position: number;
  timecode: number | null;
};

type GraphNode = {
  id: string;
  label: string;
  type: string;
  enriched: boolean;
  mentions: GraphMention[];
};

type GraphEdge = {
  source: string;
  target: string;
  label: string;
};

type GraphResponse = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  enriched: boolean;
};

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}

function normalizeStatus(raw: string | undefined): string {
  return (raw || "pending").trim().toLowerCase();
}

function normalizeProgress(raw: number | undefined): number {
  const value = Number(raw ?? 0);
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function isTerminalStatus(status: string): boolean {
  return status === "done" || status === "error";
}

function statusRank(status: string): number {
  if (status === "pending") {
    return 0;
  }
  if (status === "processing") {
    return 1;
  }
  if (status === "done" || status === "error") {
    return 2;
  }
  return 1;
}

function mergeMonotonicStatus(previous: string, incoming: string): string {
  const prev = normalizeStatus(previous);
  const next = normalizeStatus(incoming);
  if (isTerminalStatus(prev)) {
    return prev;
  }
  if (statusRank(next) < statusRank(prev)) {
    return prev;
  }
  return next;
}

export default function LecturePage() {
  const { lectureId } = useParams<{ lectureId: string }>();
  const { isAuthenticated, isLoading, token } = useAuth();
  const [liveProgress, setLiveProgress] = useState(0);
  const [liveStatus, setLiveStatus] = useState("pending");
  const [highlightedEntityLabel, setHighlightedEntityLabel] = useState<string | null>(null);
  const [selectedTimecode, setSelectedTimecode] = useState<number | null>(null);

  const lectureQuery = useQuery({
    queryKey: ["lecture", lectureId],
    enabled: Boolean(isAuthenticated && lectureId),
    queryFn: async () => {
      const response = await apiClient.get<LectureResponse>(`/lectures/${lectureId}`);
      return response.data;
    },
    refetchInterval: (query) => {
      if (query.state.status === "error") {
        return false;
      }
      const status = normalizeStatus(query.state.data?.status);
      return isTerminalStatus(status) ? false : 3000;
    },
  });

  useEffect(() => {
    setLiveProgress(0);
    setLiveStatus("pending");
    setHighlightedEntityLabel(null);
    setSelectedTimecode(null);
  }, [lectureId]);

  useEffect(() => {
    if (!lectureQuery.data) {
      return;
    }
    setLiveProgress((previous) => Math.max(previous, normalizeProgress(lectureQuery.data.processing_progress)));
    setLiveStatus((previous) => mergeMonotonicStatus(previous, lectureQuery.data.status));
  }, [lectureQuery.data]);

  const effectiveStatus = useMemo(() => liveStatus, [liveStatus]);

  const effectiveProgress = useMemo(() => {
    if (effectiveStatus === "done") {
      return 100;
    }
    return liveProgress;
  }, [effectiveStatus, liveProgress]);

  const canRenderLectureContent = !lectureQuery.isError && Boolean(lectureQuery.data);

  const summaryQuery = useQuery({
    queryKey: ["lecture-summary", lectureId],
    enabled: Boolean(isAuthenticated && lectureId && canRenderLectureContent && effectiveStatus === "done"),
    queryFn: async () => {
      const response = await apiClient.get<SummaryResponse>(`/lectures/${lectureId}/summary`);
      return response.data;
    },
  });

  const graphQuery = useQuery({
    queryKey: ["lecture-graph", lectureId],
    enabled: Boolean(isAuthenticated && lectureId && canRenderLectureContent && effectiveStatus === "done"),
    queryFn: async () => {
      const response = await apiClient.get<GraphResponse>(`/lectures/${lectureId}/graph`);
      return response.data;
    },
  });

  const handleProgress = useCallback((update: { progress: number; status: string }) => {
    setLiveProgress((previous) => Math.max(previous, normalizeProgress(update.progress)));
    setLiveStatus((previous) => mergeMonotonicStatus(previous, update.status));
  }, []);

  if (isLoading) {
    return <p>Loading...</p>;
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  if (!lectureId) {
    return (
      <section>
        <h2>Lecture</h2>
        <p>Invalid lecture id.</p>
      </section>
    );
  }

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
        <h2 style={{ margin: 0 }}>{lectureQuery.data?.title ?? "Lecture"}</h2>
        <Link to="/dashboard">Back to Dashboard</Link>
      </div>

      {lectureQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(lectureQuery.error, "Failed to load lecture details.")}
        </p>
      ) : null}

      {!canRenderLectureContent && lectureQuery.isLoading ? <p>Loading lecture details...</p> : null}

      {canRenderLectureContent ? (
        <>
          <ProgressBar
            initialProgress={effectiveProgress}
            initialStatus={effectiveStatus}
            lectureId={lectureId}
            onProgress={handleProgress}
            token={token}
          />

          {effectiveStatus !== "done" && effectiveStatus !== "error" ? (
            <p style={{ margin: 0 }}>
              Lecture is being processed. Summary and graph will appear after completion.
            </p>
          ) : null}

          {effectiveStatus === "error" ? (
            <p style={{ color: "#b00020", margin: 0 }} role="alert">
              Lecture processing failed. Please retry with another source file or URL.
            </p>
          ) : null}

          {effectiveStatus === "done" ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                gap: "1rem",
              }}
            >
              <div style={{ minWidth: 0 }}>
                {summaryQuery.isLoading ? <p>Loading summary...</p> : null}
                {summaryQuery.isError ? (
                  <p style={{ color: "#b00020", margin: 0 }} role="alert">
                    {extractErrorMessage(summaryQuery.error, "Failed to load summary.")}
                  </p>
                ) : null}
                {summaryQuery.data ? (
                  <SummaryView
                    entityLabels={(graphQuery.data?.nodes ?? []).map((node) => node.label)}
                    lectureId={lectureId}
                    onEntityClick={setHighlightedEntityLabel}
                    onTimecodeClick={setSelectedTimecode}
                    summary={summaryQuery.data}
                  />
                ) : null}
              </div>

              <div style={{ minWidth: 0 }}>
                {selectedTimecode !== null ? (
                  <p style={{ marginTop: 0 }}>
                    Selected timecode: <strong>{selectedTimecode}s</strong>
                  </p>
                ) : null}
                {graphQuery.isLoading ? <p>Loading graph...</p> : null}
                {graphQuery.isError ? (
                  <p style={{ color: "#b00020", margin: 0 }} role="alert">
                    {extractErrorMessage(graphQuery.error, "Failed to load graph.")}
                  </p>
                ) : null}
                {graphQuery.data ? (
                  <EntityGraph graph={graphQuery.data} highlightedEntityLabel={highlightedEntityLabel} />
                ) : null}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
