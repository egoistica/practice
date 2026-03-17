import { apiClient } from "./client";

export type TokenTransaction = {
  id: string;
  amount: number;
  reason: string;
  created_at: string;
};

export type TokenBalanceResponse = {
  balance: number;
  transactions: TokenTransaction[];
};

export type TokenHistoryResponse = {
  items: TokenTransaction[];
  total: number;
  skip: number;
  limit: number;
};

export function tokensBalanceQueryKey(userId: string | undefined) {
  return ["tokens-balance", userId] as const;
}

export async function fetchTokenBalance(): Promise<TokenBalanceResponse> {
  const response = await apiClient.get<TokenBalanceResponse>("/tokens/balance", {
    params: {
      include_transactions: false,
      transactions_limit: 0,
    },
  });
  return response.data;
}

export async function fetchTokenHistoryPage(skip: number, limit: number): Promise<TokenHistoryResponse> {
  const response = await apiClient.get<TokenHistoryResponse>("/tokens/history", {
    params: { skip, limit },
  });
  return response.data;
}
