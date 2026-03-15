import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import { extractErrorMessage } from "../../utils/presentation";

type TopEntity = {
  label: string;
  mentions: number;
};

type LectureRow = {
  lecture_id: string;
  title: string;
  username: string;
  status: string;
  file_size_bytes: number;
};

type AdminDbStatsResponse = {
  users_count: number;
  lectures_count: number;
  files_size_bytes: number;
  top_entities: TopEntity[];
  lectures: LectureRow[];
};

type SortField = "title" | "username" | "file_size_bytes" | "status";
type SortDirection = "asc" | "desc";

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

function sortIcon(active: boolean, direction: SortDirection): string {
  if (!active) {
    return "↕";
  }
  return direction === "asc" ? "↑" : "↓";
}

export default function DatabaseStatsPage() {
  const { user } = useAuth();
  const [sortField, setSortField] = useState<SortField>("title");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  const statsQuery = useQuery({
    queryKey: ["admin-db-stats", user?.user_id],
    enabled: Boolean(user?.is_admin),
    queryFn: async () => {
      const response = await apiClient.get<AdminDbStatsResponse>("/admin/stats/db");
      return response.data;
    },
  });

  const sortedLectures = useMemo(() => {
    const rows = [...(statsQuery.data?.lectures ?? [])];
    rows.sort((a, b) => {
      let comparison = 0;
      if (sortField === "file_size_bytes") {
        comparison = a.file_size_bytes - b.file_size_bytes;
      } else if (sortField === "title") {
        comparison = a.title.localeCompare(b.title);
      } else if (sortField === "username") {
        comparison = a.username.localeCompare(b.username);
      } else {
        comparison = a.status.localeCompare(b.status);
      }
      return sortDirection === "asc" ? comparison : -comparison;
    });
    return rows;
  }, [sortDirection, sortField, statsQuery.data?.lectures]);

  const handleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((previous) => (previous === "asc" ? "desc" : "asc"));
      return;
    }
    setSortField(field);
    setSortDirection("asc");
  };

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2 style={{ margin: 0 }}>Database Statistics</h2>

      {statsQuery.isLoading ? <p>Loading database statistics...</p> : null}
      {statsQuery.isError ? (
        <p role="alert" style={{ color: "#b00020", margin: 0 }}>
          {extractErrorMessage(statsQuery.error, "Failed to load database statistics.")}
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
              <strong>Files size</strong>
              <p style={{ fontSize: "1.2rem", margin: "0.4rem 0 0 0" }}>
                {formatStorageSize(statsQuery.data.files_size_bytes)}
              </p>
            </article>
          </div>

          <section style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
            <h3 style={{ marginTop: 0 }}>Top-10 Entities</h3>
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

          <section style={{ border: "1px solid #d6d6d6", borderRadius: "0.6rem", padding: "0.8rem" }}>
            <h3 style={{ marginTop: 0 }}>Lectures</h3>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th align="left">
                    <button onClick={() => handleSort("title")} type="button">
                      Title {sortIcon(sortField === "title", sortDirection)}
                    </button>
                  </th>
                  <th align="left">
                    <button onClick={() => handleSort("username")} type="button">
                      User {sortIcon(sortField === "username", sortDirection)}
                    </button>
                  </th>
                  <th align="left">
                    <button onClick={() => handleSort("file_size_bytes")} type="button">
                      Size {sortIcon(sortField === "file_size_bytes", sortDirection)}
                    </button>
                  </th>
                  <th align="left">
                    <button onClick={() => handleSort("status")} type="button">
                      Status {sortIcon(sortField === "status", sortDirection)}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedLectures.length === 0 ? (
                  <tr>
                    <td colSpan={4}>No lectures found.</td>
                  </tr>
                ) : (
                  sortedLectures.map((lecture) => (
                    <tr key={lecture.lecture_id}>
                      <td>{lecture.title}</td>
                      <td>{lecture.username}</td>
                      <td>{formatStorageSize(lecture.file_size_bytes)}</td>
                      <td>{lecture.status}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </section>
        </>
      ) : null}
    </section>
  );
}
