import { useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { extractErrorMessage, formatDate } from "../utils/presentation";

type FavouriteLecture = {
  lecture_id: string;
  title: string;
  status: string;
  processing_progress: number;
  created_at: string;
  favourited_at: string;
};

type FavouritesResponse = {
  items: FavouriteLecture[];
  total: number;
  skip: number;
  limit: number;
};

const PAGE_LIMIT = 100;

async function fetchFavouritesPage(skip: number, limit: number): Promise<FavouritesResponse> {
  const response = await apiClient.get<FavouritesResponse>("/favourites", {
    params: { skip, limit },
  });
  return response.data;
}

export default function FavouritesPage() {
  const { user, isLoading } = useAuth();
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set());
  const [items, setItems] = useState<FavouriteLecture[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const userId = user?.user_id;

  const favouritesQuery = useQuery({
    queryKey: ["favourites-page", userId],
    enabled: Boolean(user),
    queryFn: async () => fetchFavouritesPage(0, PAGE_LIMIT),
  });

  useEffect(() => {
    if (!favouritesQuery.data) {
      return;
    }
    setItems(favouritesQuery.data.items);
    setTotal(favouritesQuery.data.total);
  }, [favouritesQuery.data]);

  const removeMutation = useMutation({
    mutationFn: async (lectureId: string) => {
      await apiClient.delete(`/favourites/${lectureId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["favourites", userId] });
    },
  });

  async function handleRemove(lectureId: string) {
    if (removingIds.has(lectureId)) {
      return;
    }
    setActionError(null);
    setRemovingIds((previous) => {
      const next = new Set(previous);
      next.add(lectureId);
      return next;
    });

    try {
      await removeMutation.mutateAsync(lectureId);
      setItems((previous) => previous.filter((item) => item.lecture_id !== lectureId));
      setTotal((previous) => Math.max(0, previous - 1));
    } catch (error) {
      setActionError(extractErrorMessage(error, "Failed to remove from favourites."));
    } finally {
      setRemovingIds((previous) => {
        const next = new Set(previous);
        next.delete(lectureId);
        return next;
      });
    }
  }

  async function handleLoadMore() {
    if (isLoadingMore || items.length >= total) {
      return;
    }
    setActionError(null);
    setIsLoadingMore(true);
    try {
      const page = await fetchFavouritesPage(items.length, PAGE_LIMIT);
      setItems((previous) => [...previous, ...page.items]);
      setTotal(page.total);
    } catch (error) {
      setActionError(extractErrorMessage(error, "Failed to load more favourites."));
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
      <h2>Favourites</h2>

      {actionError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {actionError}
        </p>
      ) : null}

      {favouritesQuery.isLoading ? <p>Loading favourites...</p> : null}
      {favouritesQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(favouritesQuery.error, "Failed to load favourites.")}
        </p>
      ) : null}

      {!favouritesQuery.isLoading && !favouritesQuery.isError && items.length === 0 ? (
        <p>You have no favourite lectures yet.</p>
      ) : null}

      {!favouritesQuery.isLoading && !favouritesQuery.isError && items.length > 0 ? (
        <>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th align="left">Title</th>
                <th align="left">Status</th>
                <th align="left">Progress</th>
                <th align="left">Added</th>
                <th align="left">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.lecture_id}>
                  <td>{item.title}</td>
                  <td>{item.status}</td>
                  <td>{item.processing_progress}%</td>
                  <td>{formatDate(item.favourited_at)}</td>
                  <td style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <Link to={`/lecture/${item.lecture_id}`}>Open</Link>
                    <button
                      disabled={removingIds.has(item.lecture_id)}
                      onClick={() => handleRemove(item.lecture_id)}
                      type="button"
                    >
                      {removingIds.has(item.lecture_id) ? "Removing..." : "Remove"}
                    </button>
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
