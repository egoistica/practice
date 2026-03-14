import { Link } from "react-router-dom";

export default function AdminStatisticsPage() {
  return (
    <section style={{ display: "grid", gap: "0.75rem" }}>
      <h2>Admin Statistics</h2>
      <p>Statistics page placeholder for extended charts and trends.</p>
      <p style={{ margin: 0 }}>
        <Link to="/admin">Back to Admin Dashboard</Link>
      </p>
    </section>
  );
}
