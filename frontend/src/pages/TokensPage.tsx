import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  fetchTokenBalance,
  fetchTokenHistoryPage,
  fetchTokenOperationCosts,
  TokenOperationCost,
  TokenTransaction,
  tokensBalanceQueryKey,
} from "../api/tokens";
import { useAuth } from "../hooks/useAuth";
import { extractErrorMessage, formatDate } from "../utils/presentation";

type TokenHistoryRow = TokenTransaction & {
  balance_after?: number;
};

const PAGE_LIMIT = 50;

const DEFAULT_OPERATION_COSTS: TokenOperationCost[] = [
  { action: "Транскрибация", cost: 50 },
  { action: "Суммаризация", cost: 30 },
  { action: "Извлечение сущностей", cost: 40 },
  { action: "Обогащение", cost: 25 },
];

function normalizeAmount(raw: number): number {
  if (!Number.isFinite(raw)) {
    return 0;
  }
  return raw;
}

export default function TokensPage() {
  const { user, isLoading } = useAuth();
  const [items, setItems] = useState<TokenTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const userId = user?.user_id;

  const balanceQuery = useQuery({
    queryKey: tokensBalanceQueryKey(userId),
    enabled: Boolean(userId),
    queryFn: fetchTokenBalance,
  });

  const historyQuery = useQuery({
    queryKey: ["tokens-history-page", userId],
    enabled: Boolean(userId),
    queryFn: async () => fetchTokenHistoryPage(0, PAGE_LIMIT),
  });

  const operationCostsQuery = useQuery({
    queryKey: ["tokens-operation-costs"],
    enabled: Boolean(userId),
    queryFn: fetchTokenOperationCosts,
  });

  useEffect(() => {
    if (!historyQuery.data) {
      return;
    }
    setItems(historyQuery.data.items);
    setTotal(historyQuery.data.total);
    setLoadMoreError(null);
  }, [historyQuery.data]);

  const historyRows = useMemo<TokenHistoryRow[]>(() => {
    if (balanceQuery.data?.balance === undefined) {
      return items.map((item) => ({
        ...item,
        balance_after: undefined,
      }));
    }
    const currentBalance = balanceQuery.data.balance;
    let runningBalance = currentBalance;
    return items.map((item) => {
      const row: TokenHistoryRow = {
        ...item,
        balance_after: runningBalance,
      };
      runningBalance -= normalizeAmount(item.amount);
      return row;
    });
  }, [balanceQuery.data?.balance, items]);

  async function handleLoadMore() {
    if (isLoadingMore || items.length >= total) {
      return;
    }
    setLoadMoreError(null);
    setIsLoadingMore(true);
    try {
      const page = await fetchTokenHistoryPage(items.length, PAGE_LIMIT);
      setItems((previous) => [...previous, ...page.items]);
      setTotal(page.total);
    } catch (error) {
      setLoadMoreError(extractErrorMessage(error, "Не удалось загрузить историю токенов."));
    } finally {
      setIsLoadingMore(false);
    }
  }

  if (isLoading) {
    return <p>Loading...</p>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <h2 style={{ margin: 0 }}>Токены</h2>

      <div
        style={{
          display: "grid",
          gap: "0.75rem",
          padding: "1rem",
          borderRadius: "0.75rem",
          border: "1px solid #d9e2ec",
          background: "#f8fafc",
        }}
      >
        <p style={{ margin: 0, fontSize: "1.2rem", fontWeight: 700 }}>
          Текущий баланс:{" "}
          {balanceQuery.isLoading ? "..." : balanceQuery.data?.balance ?? 0} 💰
        </p>
        {balanceQuery.isError ? (
          <p role="alert" style={{ margin: 0, color: "#b00020" }}>
            {extractErrorMessage(balanceQuery.error, "Не удалось загрузить баланс.")}
          </p>
        ) : null}
      </div>

      <section style={{ display: "grid", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>Стоимость операций</h3>
        {operationCostsQuery.isLoading ? <p>Загрузка стоимости операций...</p> : null}
        {operationCostsQuery.isError ? (
          <p role="alert" style={{ color: "#b00020", margin: 0 }}>
            {extractErrorMessage(operationCostsQuery.error, "Не удалось загрузить стоимость операций.")}
            {" Используются значения по умолчанию."}
          </p>
        ) : null}
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th align="left">Операция</th>
              <th align="right">Стоимость</th>
            </tr>
          </thead>
          <tbody>
            {(operationCostsQuery.data?.length ? operationCostsQuery.data : DEFAULT_OPERATION_COSTS).map((item) => (
              <tr key={item.action}>
                <td>{item.action}</td>
                <td align="right">{item.cost}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section style={{ display: "grid", gap: "0.5rem" }}>
        <h3 style={{ margin: 0 }}>История транзакций</h3>
        {historyQuery.isLoading ? <p>Загрузка истории...</p> : null}
        {historyQuery.isError ? (
          <p role="alert" style={{ color: "#b00020", margin: 0 }}>
            {extractErrorMessage(historyQuery.error, "Не удалось загрузить историю транзакций.")}
          </p>
        ) : null}
        {loadMoreError ? (
          <p role="alert" style={{ color: "#b00020", margin: 0 }}>
            {loadMoreError}
          </p>
        ) : null}

        {!historyQuery.isLoading && !historyQuery.isError && historyRows.length === 0 ? (
          <p>Транзакций пока нет.</p>
        ) : null}

        {!historyQuery.isLoading && !historyQuery.isError && historyRows.length > 0 ? (
          <>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th align="left">Дата</th>
                  <th align="left">Действие</th>
                  <th align="right">Сумма</th>
                  <th align="right">Остаток</th>
                </tr>
              </thead>
              <tbody>
                {historyRows.map((row) => {
                  const amount = normalizeAmount(row.amount);
                  const sign = amount > 0 ? "+" : "";
                  return (
                    <tr key={row.id}>
                      <td>{formatDate(row.created_at)}</td>
                      <td>{row.reason}</td>
                      <td align="right" style={{ color: amount < 0 ? "#b00020" : "#0f5132" }}>
                        {sign}
                        {amount}
                      </td>
                      <td align="right">{row.balance_after ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {items.length < total ? (
              <button disabled={isLoadingMore} onClick={handleLoadMore} type="button">
                {isLoadingMore ? "Загрузка..." : "Ещё"}
              </button>
            ) : null}
          </>
        ) : null}
      </section>
    </section>
  );
}
