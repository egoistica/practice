import axios from "axios";
import { ChangeEvent, DragEvent, FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set([".mp4", ".avi", ".mkv", ".mov"]);
const ENTITY_OPTIONS = [
  { value: "term", label: "Термины" },
  { value: "person", label: "Персоналии" },
  { value: "technology", label: "Технологии" },
  { value: "concept", label: "Концепции" },
  { value: "date", label: "Даты" },
];

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
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && typeof first.msg === "string") {
        return first.msg;
      }
    }
  }
  return "Не удалось создать лекцию. Проверьте форму и попробуйте снова.";
}

function getFileExtension(filename: string): string {
  const parts = filename.toLowerCase().split(".");
  return parts.length > 1 ? `.${parts[parts.length - 1]}` : "";
}

function validateSelectedFile(file: File): string | null {
  const extension = getFileExtension(file.name);
  if (!ALLOWED_EXTENSIONS.has(extension)) {
    return "Неподдерживаемый формат файла. Разрешены: MP4, AVI, MKV, MOV.";
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return "Файл слишком большой. Максимальный размер: 500 MB.";
  }
  return null;
}

export default function UploadPage() {
  const navigate = useNavigate();
  const { isAuthenticated, isLoading } = useAuth();
  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState<"file" | "url">("file");
  const [mode, setMode] = useState<"instant" | "realtime">("instant");
  const [sourceUrl, setSourceUrl] = useState("");
  const [selectedEntities, setSelectedEntities] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isLoading) {
    return <p>Loading...</p>;
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  const canSubmit =
    !submitting &&
    title.trim().length > 0 &&
    ((sourceType === "file" && file !== null) || (sourceType === "url" && sourceUrl.trim().length > 0));

  function handleSourceTypeChange(nextType: "file" | "url") {
    setSourceType(nextType);
    setError(null);
    if (nextType === "file") {
      setSourceUrl("");
      return;
    }
    setFile(null);
  }

  function applyPickedFile(nextFile: File | null) {
    if (!nextFile) {
      setFile(null);
      return;
    }
    const validationError = validateSelectedFile(nextFile);
    if (validationError) {
      setFile(null);
      setError(validationError);
      return;
    }
    setError(null);
    setFile(nextFile);
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    applyPickedFile(nextFile);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDraggingOver(false);
    const nextFile = event.dataTransfer.files?.[0] ?? null;
    applyPickedFile(nextFile);
  }

  function handleEntitySelectionChange(event: ChangeEvent<HTMLSelectElement>) {
    const values = Array.from(event.target.selectedOptions).map((option) => option.value);
    setSelectedEntities(values);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    if (sourceType === "url") {
      const url = sourceUrl.trim();
      try {
        const parsed = new URL(url);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          setError("URL должен начинаться с http:// или https://.");
          return;
        }
      } catch {
        setError("Введите корректный URL.");
        return;
      }
    }

    const formData = new FormData();
    formData.append("title", title.trim());
    formData.append("mode", mode);
    formData.append("source_type", sourceType);
    if (sourceType === "url") {
      formData.append("source_url", sourceUrl.trim());
    }
    if (sourceType === "file" && file) {
      formData.append("file", file);
    }
    if (selectedEntities.length > 0) {
      formData.append("selected_entities", JSON.stringify(selectedEntities));
    }

    setSubmitting(true);
    setError(null);
    try {
      const response = await apiClient.post<LectureResponse>("/lectures", formData);
      navigate(`/lecture/${response.data.id}`, { replace: true });
    } catch (submitError) {
      setError(extractErrorMessage(submitError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section style={{ display: "grid", gap: "1rem", maxWidth: "40rem" }}>
      <h2>Загрузка лекции</h2>

      <form onSubmit={handleSubmit} style={{ display: "grid", gap: "0.9rem" }}>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Название лекции
          <input
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Например: Лекция 3 — Алгоритмы графов"
            required
            type="text"
            value={title}
          />
        </label>

        <fieldset style={{ display: "grid", gap: "0.4rem" }}>
          <legend>Источник</legend>
          <label>
            <input
              checked={sourceType === "file"}
              name="sourceType"
              onChange={() => handleSourceTypeChange("file")}
              type="radio"
              value="file"
            />{" "}
            Файл
          </label>
          <label>
            <input
              checked={sourceType === "url"}
              name="sourceType"
              onChange={() => handleSourceTypeChange("url")}
              type="radio"
              value="url"
            />{" "}
            URL (YouTube, VK Video)
          </label>
        </fieldset>

        {sourceType === "file" ? (
          <div style={{ display: "grid", gap: "0.5rem" }}>
            <label htmlFor="upload-file-input">Видео файл</label>
            <input
              accept=".mp4,.avi,.mkv,.mov,video/mp4,video/x-msvideo,video/x-matroska,video/quicktime"
              id="upload-file-input"
              onChange={handleFileInputChange}
              type="file"
            />
            <div
              onDragEnter={(event) => {
                event.preventDefault();
                setIsDraggingOver(true);
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                setIsDraggingOver(false);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDraggingOver(true);
              }}
              onDrop={handleDrop}
              style={{
                border: `2px dashed ${isDraggingOver ? "#2563eb" : "#9ca3af"}`,
                borderRadius: "0.75rem",
                padding: "1rem",
                background: isDraggingOver ? "#eff6ff" : "#f9fafb",
              }}
            >
              Перетащите видео сюда или выберите файл через поле выше.
            </div>
            <small>Форматы: MP4, AVI, MKV, MOV. Максимальный размер: 500 MB.</small>
            {file ? (
              <p style={{ margin: 0 }}>
                Выбран файл: <strong>{file.name}</strong>
              </p>
            ) : null}
          </div>
        ) : (
          <label style={{ display: "grid", gap: "0.25rem" }}>
            Ссылка на видео
            <input
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="https://youtube.com/watch?v=..."
              required
              type="url"
              value={sourceUrl}
            />
          </label>
        )}

        <fieldset style={{ display: "grid", gap: "0.4rem" }}>
          <legend>Режим обработки</legend>
          <label>
            <input
              checked={mode === "instant"}
              name="mode"
              onChange={() => setMode("instant")}
              type="radio"
              value="instant"
            />{" "}
            Мгновенный конспект
          </label>
          <label>
            <input
              checked={mode === "realtime"}
              name="mode"
              onChange={() => setMode("realtime")}
              type="radio"
              value="realtime"
            />{" "}
            По мере просмотра
          </label>
        </fieldset>

        <label style={{ display: "grid", gap: "0.25rem" }}>
          Нужные сущности (опционально)
          <select multiple onChange={handleEntitySelectionChange} size={5} value={selectedEntities}>
            {ENTITY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        {error ? (
          <p style={{ color: "#b00020", margin: 0 }} role="alert">
            {error}
          </p>
        ) : null}

        <button disabled={!canSubmit} type="submit">
          {submitting ? "Загрузка..." : "Загрузить"}
        </button>
      </form>
    </section>
  );
}
