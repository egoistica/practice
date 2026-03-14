import { Link } from "react-router-dom";

export default function AdminUsersPage() {
  return (
    <section style={{ display: "grid", gap: "0.75rem" }}>
      <h2>Admin Users</h2>
      <p>User management page placeholder. Backend endpoints are available under <code>/admin/users</code>.</p>
      <p style={{ margin: 0 }}>
        <Link to="/admin">Back to Admin Dashboard</Link>
      </p>
    </section>
  );
}
