import axios from "axios";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

type LectureResponse = {
  id: string;
  title: string;
  status: string;
  processing_progress: number;
  created_at: string;
};

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  }
  return "Не удалось загрузить лекцию.";
}

export default function LectureDetailsPage() {
  const { lectureId } = useParams<{ lectureId: string }>();
  const { isAuthenticated, isLoading } = useAuth();

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
      const status = query.state.data?.status;
      if (!status) {
        return 3000;
      }
      return status === "done" || status === "error" ? false : 3000;
    },
  });

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
        <p>Некорректный идентификатор лекции.</p>
      </section>
    );
  }

  return (
    <section style={{ display: "grid", gap: "0.75rem" }}>
      <h2>Лекция</h2>

      {lectureQuery.isLoading ? <p>Загружаем данные лекции...</p> : null}

      {lectureQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(lectureQuery.error)}
        </p>
      ) : null}

      {lectureQuery.data ? (
        <article style={{ border: "1px solid #d6d6d6", borderRadius: "0.75rem", padding: "1rem" }}>
          <p style={{ marginTop: 0 }}>
            <strong>ID:</strong> {lectureQuery.data.id}
          </p>
          <p>
            <strong>Название:</strong> {lectureQuery.data.title}
          </p>
          <p>
            <strong>Статус:</strong> {lectureQuery.data.status}
          </p>
          <p>
            <strong>Прогресс:</strong> {lectureQuery.data.processing_progress}%
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>Создана:</strong> {new Date(lectureQuery.data.created_at).toLocaleString()}
          </p>
        </article>
      ) : null}

      <p style={{ margin: 0 }}>
        <Link to="/dashboard">Вернуться в Dashboard</Link>
      </p>
    </section>
  );
}
