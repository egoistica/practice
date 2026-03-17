import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

type TokenBalanceResponse = {
  balance: number;
  transactions: Array<{
    id: string;
    amount: number;
    reason: string;
    created_at: string;
  }>;
};

async function fetchTokenBalance(): Promise<TokenBalanceResponse> {
  const response = await apiClient.get<TokenBalanceResponse>("/tokens/balance", {
    params: {
      include_transactions: false,
      transactions_limit: 0,
    },
  });
  return response.data;
}

export default function TokenBadge() {
  const { user, isLoading: isAuthLoading } = useAuth();
  const userId = user?.user_id;
  const balanceQuery = useQuery({
    queryKey: ["tokens-balance", userId],
    enabled: Boolean(userId),
    queryFn: fetchTokenBalance,
    refetchInterval: 30_000,
  });

  if (isAuthLoading || !userId) {
    return null;
  }

  const content = balanceQuery.isLoading
    ? "Баланс: ..."
    : balanceQuery.isError
      ? "Баланс: !"
      : `Баланс: ${balanceQuery.data?.balance ?? 0}`;

  return (
    <Link
      aria-label="Open tokens page"
      style={{
        textDecoration: "none",
        padding: "0.35rem 0.6rem",
        borderRadius: "999px",
        background: "#f2f4f8",
        color: "#0f172a",
        fontWeight: 600,
        border: "1px solid #d9e2ec",
        whiteSpace: "nowrap",
      }}
      to="/tokens"
      title="Перейти к токенам"
    >
      {content} 💰
    </Link>
  );
}
