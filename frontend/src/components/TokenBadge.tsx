import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchTokenBalance, tokensBalanceQueryKey } from "../api/tokens";
import { useAuth } from "../hooks/useAuth";

export default function TokenBadge() {
  const { user, isLoading: isAuthLoading } = useAuth();
  const userId = user?.user_id;
  const balanceQuery = useQuery({
    queryKey: tokensBalanceQueryKey(userId),
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
      aria-label="Открыть страницу токенов"
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
