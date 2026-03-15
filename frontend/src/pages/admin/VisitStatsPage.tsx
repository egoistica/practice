import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { apiClient } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import { extractErrorMessage, formatDate } from "../../utils/presentation";

type FilterMode = "all" | "specific";

type AdminUserOption = {
  id: string;
  username: string;
  email: string;
};

type AdminUsersListResponse = {
  items: AdminUserOption[];
  total: number;
  skip: number;
  limit: number;
};

type DailyVisitStat = {
  date: string;
  visits: number;
};

type LectureVisitStat = {
  lecture_id: string;
  lecture_title: string;
  visits: number;
  last_visited_at: string;
};

type AdminVisitsStatsResponse = {
  start_date: string;
  end_date: string;
  user_id: string | null;
  total_visits: number;
  daily_visits: DailyVisitStat[];
  lecture_visits: LectureVisitStat[];
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

export default function VisitStatsPage() {
  const { user } = useAuth();
  const defaults = useMemo(() => defaultDateRange(), []);
  const [startDate, setStartDate] = useState(defaults.start);
  const [endDate, setEndDate] = useState(defaults.end);
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [selectedUserId, setSelectedUserId] = useState<string>("");

  const rangeIsValid = isValidDateRange(startDate, endDate);
  const resolvedUserId = filterMode === "specific" ? selectedUserId : "";
  const canLoadVisits = rangeIsValid && (filterMode === "all" || Boolean(resolvedUserId));

  const usersQuery = useQuery({
    queryKey: ["admin-users-options", user?.user_id],
    enabled: Boolean(user?.is_admin),
    queryFn: async () => {
      const response = await apiClient.get<AdminUsersListResponse>("/admin/users", {
        params: { skip: 0, limit: 100 },
      });
      return response.data;
    },
  });

  const visitsQuery = useQuery({
    queryKey: ["admin-visit-stats", user?.user_id, startDate, endDate, resolvedUserId || "all"],
    enabled: Boolean(user?.is_admin && canLoadVisits),
    queryFn: async () => {
      const response = await apiClient.get<AdminVisitsStatsResponse>("/admin/stats/visits", {
        params: {
          start_date: startDate,
          end_date: endDate,
          ...(resolvedUserId ? { user_id: resolvedUserId } : {}),
        },
      });
      return response.data;
    },
  });

  const chartData = visitsQuery.data?.daily_visits ?? [];
  const tableData = visitsQuery.data?.lecture_visits ?? [];

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2 style={{ margin: 0 }}>Visit Statistics</h2>

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

      <div
        aria-label="Visits filter mode"
        role="group"
        style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}
      >
        <button
          aria-pressed={filterMode === "all"}
          onClick={() => {
            setFilterMode("all");
            setSelectedUserId("");
          }}
          type="button"
        >
          Все пользователи
        </button>
        <button
          aria-pressed={filterMode === "specific"}
          onClick={() => setFilterMode("specific")}
          type="button"
        >
          Конкретный пользователь
        </button>
      </div>

      {filterMode === "specific" ? (
        <label style={{ display: "grid", gap: "0.2rem", maxWidth: "420px" }}>
          Пользователь
          <select
            onChange={(event) => setSelectedUserId(event.target.value)}
            value={selectedUserId}
          >
            <option value="">Выберите пользователя</option>
            {(usersQuery.data?.items ?? []).map((userOption) => (
              <option key={userOption.id} value={userOption.id}>
                {userOption.username} ({userOption.email})
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {!rangeIsValid ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          Start date must be less than or equal to end date.
        </p>
      ) : null}
      {filterMode === "specific" && !selectedUserId ? (
        <p style={{ margin: 0 }}>Выберите пользователя, чтобы загрузить статистику.</p>
      ) : null}

      {usersQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(usersQuery.error, "Failed to load user list for filter.")}
        </p>
      ) : null}

      {visitsQuery.isLoading ? <p>Loading visit statistics...</p> : null}
      {visitsQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(visitsQuery.error, "Failed to load visit statistics.")}
        </p>
      ) : null}

      {visitsQuery.data ? (
        <>
          <p style={{ margin: 0 }}>
            Total visits in selected period: <strong>{visitsQuery.data.total_visits}</strong>
          </p>

          <div
            style={{
              width: "100%",
              height: "320px",
              border: "1px solid #d6d6d6",
              borderRadius: "0.75rem",
              padding: "0.5rem",
            }}
          >
            <ResponsiveContainer height="100%" width="100%">
              <BarChart data={chartData} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="visits" fill="#0ea5e9" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th align="left">Lecture</th>
                <th align="left">Visits</th>
                <th align="left">Last visit</th>
              </tr>
            </thead>
            <tbody>
              {tableData.length === 0 ? (
                <tr>
                  <td colSpan={3}>No visits for selected filters.</td>
                </tr>
              ) : (
                tableData.map((item) => (
                  <tr key={item.lecture_id}>
                    <td>{item.lecture_title}</td>
                    <td>{item.visits}</td>
                    <td>{formatDate(item.last_visited_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      ) : null}
    </section>
  );
}
