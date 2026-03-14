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

function isTerminalStatus(status: string): boolean {
  return status === "done" || status === "error";
}

export default function LecturePage() {
  const { lectureId } = useParams<{ lectureId: string }>();
  const { isAuthenticated, isLoading, token } = useAuth();
  const [liveProgress, setLiveProgress] = useState(0);
  const [liveStatus, setLiveStatus] = useState("pending");

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
    if (!lectureQuery.data) {
      return;
    }
    setLiveProgress(lectureQuery.data.processing_progress);
    setLiveStatus(normalizeStatus(lectureQuery.data.status));
  }, [lectureQuery.data]);

  const effectiveStatus = useMemo(() => {
    const remoteStatus = normalizeStatus(lectureQuery.data?.status);
    if (isTerminalStatus(liveStatus)) {
      return liveStatus;
    }
    return remoteStatus || liveStatus;
  }, [lectureQuery.data?.status, liveStatus]);

  const effectiveProgress = useMemo(() => {
    const remoteProgress = lectureQuery.data?.processing_progress ?? 0;
    if (effectiveStatus === "done") {
      return 100;
    }
    return Math.max(remoteProgress, liveProgress);
  }, [effectiveStatus, lectureQuery.data?.processing_progress, liveProgress]);

  const summaryQuery = useQuery({
    queryKey: ["lecture-summary", lectureId],
    enabled: Boolean(isAuthenticated && lectureId && effectiveStatus === "done"),
    queryFn: async () => {
      const response = await apiClient.get<SummaryResponse>(`/lectures/${lectureId}/summary`);
      return response.data;
    },
  });

  const graphQuery = useQuery({
    queryKey: ["lecture-graph", lectureId],
    enabled: Boolean(isAuthenticated && lectureId && effectiveStatus === "done"),
    queryFn: async () => {
      const response = await apiClient.get<GraphResponse>(`/lectures/${lectureId}/graph`);
      return response.data;
    },
  });

  const handleProgress = useCallback((update: { progress: number; status: string }) => {
    setLiveProgress(update.progress);
    setLiveStatus(normalizeStatus(update.status));
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
            {summaryQuery.data ? <SummaryView summary={summaryQuery.data} /> : null}
          </div>

          <div style={{ minWidth: 0 }}>
            {graphQuery.isLoading ? <p>Loading graph...</p> : null}
            {graphQuery.isError ? (
              <p style={{ color: "#b00020", margin: 0 }} role="alert">
                {extractErrorMessage(graphQuery.error, "Failed to load graph.")}
              </p>
            ) : null}
            {graphQuery.data ? <EntityGraph graph={graphQuery.data} /> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
