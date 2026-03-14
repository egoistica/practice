import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import { extractErrorMessage } from "../../utils/presentation";

type TopEntity = {
  label: string;
  mentions: number;
};

type AdminOverviewStats = {
  users_count: number;
  lectures_count: number;
  storage_size_bytes: number;
  top_entities: TopEntity[];
};

function formatStorageSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

export default function AdminDashboardPage() {
  const { user } = useAuth();

  const statsQuery = useQuery({
    queryKey: ["admin-overview-stats", user?.user_id],
    enabled: Boolean(user?.is_admin),
    queryFn: async () => {
      const response = await apiClient.get<AdminOverviewStats>("/admin/stats/overview");
      return response.data;
    },
  });

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2>Admin Dashboard</h2>

      <nav style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <Link to="/admin/users">Users</Link>
        <Link to="/admin/statistics">Statistics</Link>
        <Link to="/admin/database-stats">Database Stats</Link>
      </nav>

      {statsQuery.isLoading ? <p>Loading admin summary...</p> : null}
      {statsQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(statsQuery.error, "Failed to load admin summary.")}
        </p>
      ) : null}

      {statsQuery.data ? (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "0.75rem",
            }}
          >
            <article style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
              <strong>Users</strong>
              <p style={{ fontSize: "1.2rem", margin: "0.4rem 0 0 0" }}>{statsQuery.data.users_count}</p>
            </article>
            <article style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
              <strong>Lectures</strong>
              <p style={{ fontSize: "1.2rem", margin: "0.4rem 0 0 0" }}>{statsQuery.data.lectures_count}</p>
            </article>
            <article style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
              <strong>Storage size</strong>
              <p style={{ fontSize: "1.2rem", margin: "0.4rem 0 0 0" }}>
                {formatStorageSize(statsQuery.data.storage_size_bytes)}
              </p>
            </article>
          </div>

          <section style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
            <h3 style={{ marginTop: 0 }}>Top-5 Entities</h3>
            {statsQuery.data.top_entities.length === 0 ? (
              <p style={{ marginBottom: 0 }}>No entities found yet.</p>
            ) : (
              <ol style={{ margin: 0, paddingLeft: "1.25rem", display: "grid", gap: "0.25rem" }}>
                {statsQuery.data.top_entities.map((entity, index) => (
                  <li key={`${entity.label}-${index}`}>
                    {entity.label} ({entity.mentions})
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      ) : null}
    </section>
  );
}
