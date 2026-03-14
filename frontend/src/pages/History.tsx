import axios from "axios";
import { useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

type HistoryLecture = {
  lecture_id: string;
  title: string;
  status: string;
  processing_progress: number;
  created_at: string;
  visited_at: string;
};

type HistoryResponse = {
  items: HistoryLecture[];
  total: number;
  skip: number;
  limit: number;
};

const PAGE_LIMIT = 100;

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}

async function fetchAllHistory(sortOrder: "asc" | "desc"): Promise<HistoryLecture[]> {
  const allItems: HistoryLecture[] = [];
  let skip = 0;
  let total = Number.POSITIVE_INFINITY;

  while (allItems.length < total) {
    const response = await apiClient.get<HistoryResponse>("/history", {
      params: { skip, limit: PAGE_LIMIT, sort_order: sortOrder },
    });
    const page = response.data;
    total = page.total;
    allItems.push(...page.items);

    if (page.items.length === 0) {
      break;
    }
    skip += page.items.length;
  }

  return allItems;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export default function HistoryPage() {
  const { user, isLoading } = useAuth();
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const userId = user?.user_id;

  const historyQuery = useQuery({
    queryKey: ["history-page", userId, sortOrder],
    enabled: Boolean(user),
    queryFn: async () => fetchAllHistory(sortOrder),
  });

  const sortedItems = useMemo(() => {
    if (!historyQuery.data) {
      return [];
    }
    if (sortOrder === "desc") {
      return historyQuery.data;
    }
    return [...historyQuery.data].sort(
      (a, b) => new Date(a.visited_at).getTime() - new Date(b.visited_at).getTime(),
    );
  }, [historyQuery.data, sortOrder]);

  if (isLoading) {
    return <p>Loading...</p>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
        <h2 style={{ margin: 0 }}>History</h2>
        <label htmlFor="history-sort-order">
          Sort by visited date{" "}
          <select
            id="history-sort-order"
            onChange={(event) => setSortOrder(event.target.value as "asc" | "desc")}
            value={sortOrder}
          >
            <option value="desc">Newest first</option>
            <option value="asc">Oldest first</option>
          </select>
        </label>
      </div>

      {historyQuery.isLoading ? <p>Loading history...</p> : null}
      {historyQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(historyQuery.error, "Failed to load history.")}
        </p>
      ) : null}

      {!historyQuery.isLoading && !historyQuery.isError && sortedItems.length === 0 ? (
        <p>History is empty.</p>
      ) : null}

      {!historyQuery.isLoading && !historyQuery.isError && sortedItems.length > 0 ? (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th align="left">Title</th>
              <th align="left">Status</th>
              <th align="left">Progress</th>
              <th align="left">Visited at</th>
              <th align="left">Actions</th>
            </tr>
          </thead>
          <tbody>
            {sortedItems.map((item) => (
              <tr key={`${item.lecture_id}-${item.visited_at}`}>
                <td>{item.title}</td>
                <td>{item.status}</td>
                <td>{item.processing_progress}%</td>
                <td>{formatDate(item.visited_at)}</td>
                <td>
                  <Link to={`/lecture/${item.lecture_id}`}>Open</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
