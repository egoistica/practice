import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { apiClient } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import { extractErrorMessage } from "../../utils/presentation";

type DailyStat = {
  date: string;
  new_users: number;
};

type AdminUsersStatsResponse = {
  start_date: string;
  end_date: string;
  total_new_users: number;
  items: DailyStat[];
};

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function defaultDateRange(): { start: string; end: string } {
  const now = new Date();
  const end = isoDate(now);
  const startDate = new Date(now);
  startDate.setDate(startDate.getDate() - 29);
  return { start: isoDate(startDate), end };
}

function isValidDateRange(startDate: string, endDate: string): boolean {
  return Boolean(startDate && endDate && startDate <= endDate);
}

export default function UserStatsPage() {
  const { user } = useAuth();
  const defaults = useMemo(() => defaultDateRange(), []);
  const [startDate, setStartDate] = useState(defaults.start);
  const [endDate, setEndDate] = useState(defaults.end);

  const rangeIsValid = isValidDateRange(startDate, endDate);

  const statsQuery = useQuery({
    queryKey: ["admin-user-stats", user?.user_id, startDate, endDate],
    enabled: Boolean(user?.is_admin && rangeIsValid),
    queryFn: async () => {
      const response = await apiClient.get<AdminUsersStatsResponse>("/admin/stats/users", {
        params: {
          start_date: startDate,
          end_date: endDate,
        },
      });
      return response.data;
    },
  });

  const chartData = statsQuery.data?.items ?? [];

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2 style={{ margin: 0 }}>User Statistics</h2>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "end", flexWrap: "wrap" }}>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          Start date
          <input
            max={endDate}
            onChange={(event) => setStartDate(event.target.value)}
            type="date"
            value={startDate}
          />
        </label>
        <label style={{ display: "grid", gap: "0.2rem" }}>
          End date
          <input
            min={startDate}
            onChange={(event) => setEndDate(event.target.value)}
            type="date"
            value={endDate}
          />
        </label>
      </div>

      {!rangeIsValid ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          Start date must be less than or equal to end date.
        </p>
      ) : null}

      {statsQuery.isLoading ? <p>Loading user statistics...</p> : null}
      {statsQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(statsQuery.error, "Failed to load user statistics.")}
        </p>
      ) : null}

      {statsQuery.data ? (
        <>
          <p style={{ margin: 0 }}>
            New users in selected period: <strong>{statsQuery.data.total_new_users}</strong>
          </p>

          <div style={{ width: "100%", height: "320px", border: "1px solid #d6d6d6", borderRadius: "0.75rem", padding: "0.5rem" }}>
            <ResponsiveContainer height="100%" width="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Area
                  dataKey="new_users"
                  fill="#93c5fd"
                  stroke="#2563eb"
                  strokeWidth={2}
                  type="monotone"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th align="left">Date</th>
                <th align="left">New users</th>
              </tr>
            </thead>
            <tbody>
              {chartData.map((item) => (
                <tr key={item.date}>
                  <td>{item.date}</td>
                  <td>{item.new_users}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
