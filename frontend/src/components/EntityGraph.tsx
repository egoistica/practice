import { useEffect, useMemo, useRef, useState } from "react";
import { DataSet, Network } from "vis-network/standalone";

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
  graph: GraphData;
  highlightedEntityLabel?: string | null;
};

export default function EntityGraph({ graph, highlightedEntityLabel = null }: EntityGraphProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<Network | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) {
      return null;
    }
    return graph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [graph.nodes, selectedNodeId]);

  useEffect(() => {
    if (!containerRef.current || graph.nodes.length === 0) {
      return;
    }

    const nodes = new DataSet(
      graph.nodes.map((node) => ({
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
      graph.edges.map((edge) => ({
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
  }, [graph.edges, graph.nodes]);

  useEffect(() => {
    if (!highlightedEntityLabel || !networkRef.current) {
      return;
    }
    const target = graph.nodes.find(
      (node) => node.label.trim().toLowerCase() === highlightedEntityLabel.trim().toLowerCase(),
    );
    if (!target) {
      return;
    }
    setSelectedNodeId(target.id);
    networkRef.current.selectNodes([target.id]);
    networkRef.current.focus(target.id, {
      scale: 1.1,
      animation: { duration: 350, easingFunction: "easeInOutQuad" },
    });
  }, [graph.nodes, highlightedEntityLabel]);

  return (
    <section style={{ display: "grid", gap: "0.75rem", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>Entity Graph</h3>
        <span
          style={{
            fontSize: "0.9rem",
            color: graph.enriched ? "#166534" : "#374151",
            background: graph.enriched ? "#dcfce7" : "#f3f4f6",
            borderRadius: "999px",
            padding: "0.2rem 0.6rem",
          }}
        >
          {graph.enriched ? "✓ Enriched" : "Base graph"}
        </span>
      </div>

      {graph.nodes.length === 0 ? <p>No graph data yet.</p> : null}
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
          <p style={{ margin: 0 }}>
            <strong>{selectedNode.label}</strong> ({selectedNode.type})
          </p>
          <p style={{ margin: "0.35rem 0 0 0" }}>Mentions: {selectedNode.mentions.length}</p>
        </article>
      ) : (
        <p style={{ margin: 0 }}>Click a node to highlight and inspect it.</p>
      )}
    </section>
  );
}
