import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { extractErrorMessage, formatDate } from "../utils/presentation";

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

async function fetchAllHistory(skip: number, limit: number, sortOrder: "asc" | "desc"): Promise<HistoryResponse> {
  const response = await apiClient.get<HistoryResponse>("/history", {
    params: { skip, limit, sort_order: sortOrder },
  });
  return response.data;
}

export default function HistoryPage() {
  const { user, isLoading } = useAuth();
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [items, setItems] = useState<HistoryLecture[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const userId = user?.user_id;

  const historyQuery = useQuery({
    queryKey: ["history-page", userId, sortOrder],
    enabled: Boolean(user),
    queryFn: async () => fetchAllHistory(0, PAGE_LIMIT, sortOrder),
  });

  useEffect(() => {
    if (!historyQuery.data) {
      return;
    }
    setItems(historyQuery.data.items);
    setTotal(historyQuery.data.total);
    setLoadMoreError(null);
  }, [historyQuery.data]);

  async function handleLoadMore() {
    if (isLoadingMore || items.length >= total) {
      return;
    }
    setLoadMoreError(null);
    setIsLoadingMore(true);
    try {
      const page = await fetchAllHistory(items.length, PAGE_LIMIT, sortOrder);
      setItems((previous) => [...previous, ...page.items]);
      setTotal(page.total);
    } catch (error) {
      setLoadMoreError(extractErrorMessage(error, "Failed to load more history."));
    } finally {
      setIsLoadingMore(false);
    }
  }

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
      {loadMoreError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {loadMoreError}
        </p>
      ) : null}

      {!historyQuery.isLoading && !historyQuery.isError && items.length === 0 ? (
        <p>History is empty.</p>
      ) : null}

      {!historyQuery.isLoading && !historyQuery.isError && items.length > 0 ? (
        <>
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
              {items.map((item) => (
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
          {items.length < total ? (
            <button disabled={isLoadingMore} onClick={handleLoadMore} type="button">
              {isLoadingMore ? "Loading..." : "Load more"}
            </button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
