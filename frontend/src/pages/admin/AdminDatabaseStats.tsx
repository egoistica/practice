import { Link } from "react-router-dom";

export default function AdminDatabaseStatsPage() {
  return (
    <section style={{ display: "grid", gap: "0.75rem" }}>
      <h2>Admin Database Stats</h2>
      <p>Database stats page placeholder for DB-specific metrics.</p>
      <p style={{ margin: 0 }}>
        <Link to="/admin">Back to Admin Dashboard</Link>
      </p>
    </section>
  );
}
