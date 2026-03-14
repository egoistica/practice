import axios from "axios";
import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import LectureCard from "../components/LectureCard";
import { useAuth } from "../hooks/useAuth";

type LectureItem = {
  id: string;
  title: string;
  status: string;
  processing_progress: number;
  created_at: string;
};

type LectureListResponse = {
  items: LectureItem[];
  total: number;
  skip: number;
  limit: number;
};

type FavouriteItem = {
  lecture_id: string;
};

type FavouritesListResponse = {
  items: FavouriteItem[];
  total: number;
  skip: number;
  limit: number;
};

const lecturesQueryKey = ["lectures"];
const favouritesQueryKey = ["favourites"];

function extractErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return fallback;
}

export default function DashboardPage() {
  const { user, isLoading } = useAuth();
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [togglingLectureId, setTogglingLectureId] = useState<string | null>(null);

  const lecturesQuery = useQuery({
    queryKey: lecturesQueryKey,
    enabled: Boolean(user),
    queryFn: async () => {
      const response = await apiClient.get<LectureListResponse>("/lectures", {
        params: { skip: 0, limit: 100, sort_order: "desc" },
      });
      return response.data;
    },
  });

  const favouritesQuery = useQuery({
    queryKey: favouritesQueryKey,
    enabled: Boolean(user),
    queryFn: async () => {
      const response = await apiClient.get<FavouritesListResponse>("/favourites", {
        params: { skip: 0, limit: 100 },
      });
      return response.data;
    },
  });

  const addFavouriteMutation = useMutation({
    mutationFn: async (lectureId: string) => {
      await apiClient.post(`/favourites/${lectureId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: favouritesQueryKey });
    },
  });

  const removeFavouriteMutation = useMutation({
    mutationFn: async (lectureId: string) => {
      await apiClient.delete(`/favourites/${lectureId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: favouritesQueryKey });
    },
  });

  if (isLoading) {
    return (
      <section>
        <h2>Dashboard</h2>
        <p>Loading...</p>
      </section>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  const lectures = lecturesQuery.data?.items ?? [];
  const statusOptions = ["all", ...Array.from(new Set<string>(lectures.map((lecture) => lecture.status)))];
  const favouriteLectureIds = new Set((favouritesQuery.data?.items ?? []).map((item) => item.lecture_id));
  const normalizedSearch = searchText.trim().toLowerCase();
  const filteredLectures = lectures.filter((lecture) => {
    const matchesSearch = normalizedSearch.length === 0 || lecture.title.toLowerCase().includes(normalizedSearch);
    const matchesStatus = statusFilter === "all" || lecture.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  async function handleToggleFavourite(lectureId: string, currentlyFavourite: boolean) {
    setErrorMessage(null);
    setTogglingLectureId(lectureId);
    try {
      if (currentlyFavourite) {
        await removeFavouriteMutation.mutateAsync(lectureId);
      } else {
        await addFavouriteMutation.mutateAsync(lectureId);
      }
    } catch (error) {
      setErrorMessage(extractErrorMessage(error, "Failed to update favourite state."));
    } finally {
      setTogglingLectureId(null);
    }
  }

  const isFetching = lecturesQuery.isLoading || favouritesQuery.isLoading;

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2>Dashboard</h2>
      <p style={{ margin: 0 }}>Welcome, {user.username}.</p>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <input
          onChange={(event) => setSearchText(event.target.value)}
          placeholder="Search by lecture title"
          type="search"
          value={searchText}
        />
        <select onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
          {statusOptions.map((option) => (
            <option key={option} value={option}>
              {option === "all" ? "All statuses" : option}
            </option>
          ))}
        </select>
        <Link to="/upload">Upload New Lecture</Link>
      </div>

      {errorMessage ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {errorMessage}
        </p>
      ) : null}

      {lecturesQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(lecturesQuery.error, "Failed to load lectures.")}
        </p>
      ) : null}

      {favouritesQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(favouritesQuery.error, "Failed to load favourites.")}
        </p>
      ) : null}

      {isFetching ? <p>Loading lectures...</p> : null}

      {!isFetching && filteredLectures.length === 0 ? <p>No lectures found.</p> : null}

      {!isFetching ? (
        <div style={{ display: "grid", gap: "0.75rem" }}>
          {filteredLectures.map((lecture) => (
            <LectureCard
              key={lecture.id}
              isFavourite={favouriteLectureIds.has(lecture.id)}
              isTogglingFavourite={togglingLectureId === lecture.id}
              lecture={lecture}
              onToggleFavourite={handleToggleFavourite}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
