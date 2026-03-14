import axios from "axios";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { DataSet, Network } from "vis-network/standalone";

import { apiClient } from "../api/client";

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

type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  enriched: boolean;
};

type EntityGraphProps = {
  lectureId: string;
  graph: GraphData;
  highlightedEntityLabel?: string | null;
  onTimecodeClick?: (seconds: number) => void;
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

function formatTimecode(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) {
    return "-";
  }
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function parseFilename(contentDisposition: string | undefined, fallback: string): string {
  if (!contentDisposition) {
    return fallback;
  }
  const utfMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch && utfMatch[1]) {
    try {
      return decodeURIComponent(utfMatch[1].replace(/"/g, ""));
    } catch {
      return utfMatch[1].replace(/"/g, "");
    }
  }
  const regularMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  if (regularMatch && regularMatch[1]) {
    return regularMatch[1];
  }
  return fallback;
}

export default function EntityGraph({
  lectureId,
  graph,
  highlightedEntityLabel = null,
  onTimecodeClick,
}: EntityGraphProps) {
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<Network | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [activeTypes, setActiveTypes] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingFormat, setDownloadingFormat] = useState<"json" | "png" | null>(null);

  const nodeTypes = useMemo(() => {
    return Array.from(new Set(graph.nodes.map((node) => node.type))).sort((a, b) => a.localeCompare(b));
  }, [graph.nodes]);

  useEffect(() => {
    setActiveTypes((previous) => {
      if (previous.length === 0) {
        return nodeTypes;
      }
      const previousSet = new Set(previous);
      const next = previous.filter((type) => nodeTypes.includes(type));
      for (const type of nodeTypes) {
        if (!previousSet.has(type)) {
          next.push(type);
        }
      }
      return next;
    });
  }, [nodeTypes]);

  const activeTypeSet = useMemo(() => new Set(activeTypes), [activeTypes]);
  const filteredNodes = useMemo(
    () => graph.nodes.filter((node) => activeTypeSet.has(node.type)),
    [activeTypeSet, graph.nodes],
  );
  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((node) => node.id)), [filteredNodes]);
  const filteredEdges = useMemo(
    () =>
      graph.edges.filter((edge) => {
        return filteredNodeIds.has(edge.source) && filteredNodeIds.has(edge.target);
      }),
    [filteredNodeIds, graph.edges],
  );

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) {
      return null;
    }
    return filteredNodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [filteredNodes, selectedNodeId]);

  const enrichMutation = useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<GraphData>(`/lectures/${lectureId}/graph/enrich`, {});
      return response.data;
    },
    onSuccess: (updatedGraph) => {
      setActionError(null);
      queryClient.setQueryData(["lecture-graph", lectureId], updatedGraph);
    },
    onError: (error) => {
      setActionError(extractErrorMessage(error, "Failed to enrich graph."));
    },
  });

  useEffect(() => {
    if (!containerRef.current || filteredNodes.length === 0) {
      return;
    }

    const nodes = new DataSet(
      filteredNodes.map((node) => ({
        id: node.id,
        label: node.label,
        title: `${node.type}${node.enriched ? " (enriched)" : ""}`,
        shape: "dot",
        size: node.enriched ? 18 : 14,
        color: node.enriched
          ? { background: "#d1fae5", border: "#059669", highlight: { background: "#a7f3d0", border: "#047857" } }
          : { background: "#dbeafe", border: "#2563eb", highlight: { background: "#bfdbfe", border: "#1d4ed8" } },
      })),
    );
    const edges = new DataSet(
      filteredEdges.map((edge) => ({
        from: edge.source,
        to: edge.target,
        label: edge.label,
        arrows: "to",
      })),
    );

    const network = new Network(
      containerRef.current,
      { nodes, edges },
      {
        autoResize: true,
        interaction: {
          dragNodes: true,
          dragView: true,
          zoomView: true,
          hover: true,
        },
        nodes: {
          font: { size: 14, face: "Arial" },
          borderWidth: 1,
        },
        edges: {
          smooth: { enabled: true, type: "dynamic" },
          color: { color: "#6b7280", highlight: "#111827" },
          font: { align: "middle", size: 11 },
        },
        physics: {
          stabilization: true,
          barnesHut: { gravitationalConstant: -7000, springLength: 140 },
        },
      },
    );

    network.on("click", (params) => {
      const clickedNode = params.nodes[0];
      if (clickedNode === undefined || clickedNode === null) {
        setSelectedNodeId(null);
        return;
      }
      setSelectedNodeId(String(clickedNode));
    });
    networkRef.current = network;

    return () => {
      networkRef.current = null;
      network.destroy();
    };
  }, [filteredEdges, filteredNodes]);

  useEffect(() => {
    if (!highlightedEntityLabel) {
      return;
    }
    const target = graph.nodes.find(
      (node) => node.label.trim().toLowerCase() === highlightedEntityLabel.trim().toLowerCase(),
    );
    if (!target) {
      return;
    }
    setActiveTypes((previous) => {
      if (previous.includes(target.type)) {
        return previous;
      }
      return [...previous, target.type];
    });
    setSelectedNodeId(target.id);
  }, [graph.nodes, highlightedEntityLabel]);

  useEffect(() => {
    if (!highlightedEntityLabel || !networkRef.current) {
      return;
    }
    const target = filteredNodes.find(
      (node) => node.label.trim().toLowerCase() === highlightedEntityLabel.trim().toLowerCase(),
    );
    if (!target) {
      return;
    }
    networkRef.current.selectNodes([target.id]);
    networkRef.current.focus(target.id, {
      scale: 1.1,
      animation: { duration: 350, easingFunction: "easeInOutQuad" },
    });
  }, [filteredNodes, highlightedEntityLabel]);

  async function handleExport(format: "json" | "png") {
    setActionError(null);
    setDownloadingFormat(format);
    try {
      const response = await apiClient.get(`/lectures/${lectureId}/graph/export`, {
        params: { format },
        responseType: "blob",
      });
      const fallbackName = `lecture-${lectureId}-graph.${format}`;
      const filename = parseFilename(response.headers["content-disposition"], fallbackName);
      const blobUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      setActionError(extractErrorMessage(error, `Failed to export graph as ${format.toUpperCase()}.`));
    } finally {
      setDownloadingFormat(null);
    }
  }

  function toggleType(type: string) {
    setActiveTypes((previous) => {
      if (previous.includes(type)) {
        return previous.filter((item) => item !== type);
      }
      return [...previous, type];
    });
  }

  const selectedNodeMentions = selectedNode?.mentions ?? [];

  return (
    <section style={{ display: "grid", gap: "0.75rem", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>Entity Graph</h3>
        <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <span
            style={{
              fontSize: "0.9rem",
              color: graph.enriched ? "#166534" : "#374151",
              background: graph.enriched ? "#dcfce7" : "#f3f4f6",
              borderRadius: "999px",
              padding: "0.2rem 0.6rem",
            }}
          >
            {graph.enriched ? "Enriched" : "Base graph"}
          </span>
          <button disabled={enrichMutation.isPending} onClick={() => enrichMutation.mutate()} type="button">
            {enrichMutation.isPending ? "Enriching..." : "✓ Enrich"}
          </button>
          <button disabled={downloadingFormat === "json"} onClick={() => handleExport("json")} type="button">
            {downloadingFormat === "json" ? "Downloading..." : "⇩ JSON"}
          </button>
          <button disabled={downloadingFormat === "png"} onClick={() => handleExport("png")} type="button">
            {downloadingFormat === "png" ? "Downloading..." : "⇩ PNG"}
          </button>
        </div>
      </div>

      {actionError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {actionError}
        </p>
      ) : null}

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
        {nodeTypes.map((type) => {
          const enabled = activeTypeSet.has(type);
          return (
            <button
              key={type}
              onClick={() => toggleType(type)}
              style={{
                border: "1px solid #d1d5db",
                borderRadius: "999px",
                background: enabled ? "#e0ecff" : "#f9fafb",
                color: enabled ? "#1d4ed8" : "#374151",
                padding: "0.2rem 0.6rem",
              }}
              type="button"
            >
              {enabled ? "●" : "○"} {type}
            </button>
          );
        })}
      </div>

      {graph.nodes.length === 0 ? <p>No graph data yet.</p> : null}
      {graph.nodes.length > 0 && filteredNodes.length === 0 ? (
        <p style={{ margin: 0 }}>No nodes match current type filters.</p>
      ) : null}
      <div
        ref={containerRef}
        style={{
          height: "28rem",
          border: "1px solid #d6d6d6",
          borderRadius: "0.75rem",
          background: "#ffffff",
        }}
      />

      {selectedNode ? (
        <article style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.65rem" }}>
          <p style={{ margin: 0, display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <strong>{selectedNode.label}</strong> ({selectedNode.type})
            {selectedNode.enriched ? <span style={{ color: "#166534" }}>enriched</span> : null}
          </p>
          <p style={{ margin: "0.35rem 0 0.45rem 0" }}>Mentions: {selectedNodeMentions.length}</p>
          {selectedNodeMentions.length === 0 ? (
            <p style={{ margin: 0 }}>No mention anchors available for this entity.</p>
          ) : (
            <ul style={{ margin: 0, paddingLeft: "1.1rem", display: "grid", gap: "0.25rem" }}>
              {selectedNodeMentions.map((mention, index) => {
                const seconds = mention.timecode;
                return (
                  <li key={`${selectedNode.id}-mention-${index}`}>
                    position {mention.position}
                    {seconds !== null ? (
                      <>
                        {" "}
                        at{" "}
                        {onTimecodeClick ? (
                          <button onClick={() => onTimecodeClick(Math.floor(seconds))} type="button">
                            {formatTimecode(seconds)}
                          </button>
                        ) : (
                          <span>{formatTimecode(seconds)}</span>
                        )}
                      </>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </article>
      ) : (
        <p style={{ margin: 0 }}>Click a node to highlight and inspect it.</p>
      )}
    </section>
  );
}
