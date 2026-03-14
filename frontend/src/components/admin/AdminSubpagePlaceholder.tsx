import { Link } from "react-router-dom";

type AdminSubpagePlaceholderProps = {
  title: string;
  description: string;
  backTo?: string;
};

export default function AdminSubpagePlaceholder({
  title,
  description,
  backTo = "/admin",
}: AdminSubpagePlaceholderProps) {
  return (
    <section style={{ display: "grid", gap: "0.75rem" }}>
      <h2>{title}</h2>
      <p>{description}</p>
      <p style={{ margin: 0 }}>
        <Link to={backTo}>Back to Admin Dashboard</Link>
      </p>
    </section>
  );
}
