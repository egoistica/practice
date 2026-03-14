type LectureCardProps = {
  lecture: {
    id: string;
    title: string;
    status: string;
    processing_progress: number;
    created_at: string;
  };
  isFavourite: boolean;
  isTogglingFavourite: boolean;
  onToggleFavourite: (lectureId: string, currentlyFavourite: boolean) => void;
};

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export default function LectureCard({
  lecture,
  isFavourite,
  isTogglingFavourite,
  onToggleFavourite,
}: LectureCardProps) {
  return (
    <article
      style={{
        border: "1px solid #d6d6d6",
        borderRadius: "0.75rem",
        padding: "1rem",
        display: "grid",
        gap: "0.5rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "flex-start" }}>
        <h3 style={{ margin: 0 }}>{lecture.title}</h3>
        <button
          aria-label={isFavourite ? "Remove from favourites" : "Add to favourites"}
          disabled={isTogglingFavourite}
          onClick={() => onToggleFavourite(lecture.id, isFavourite)}
          style={{
            border: "1px solid #d6d6d6",
            background: isFavourite ? "#fff6cc" : "#ffffff",
            borderRadius: "0.5rem",
            cursor: isTogglingFavourite ? "not-allowed" : "pointer",
            padding: "0.25rem 0.5rem",
            lineHeight: 1,
            minWidth: "2.5rem",
          }}
          type="button"
        >
          {isFavourite ? "★" : "☆"}
        </button>
      </div>

      <p style={{ margin: 0 }}>
        <strong>Status:</strong> {lecture.status}
      </p>
      <p style={{ margin: 0 }}>
        <strong>Progress:</strong> {lecture.processing_progress}%
      </p>
      <p style={{ margin: 0 }}>
        <strong>Created:</strong> {formatDate(lecture.created_at)}
      </p>
    </article>
  );
}
