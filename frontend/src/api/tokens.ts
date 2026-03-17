import { apiClient } from "./client";

export type TokenTransaction = {
  id: string;
  amount: number;
  reason: string;
  created_at: string;
};

export type TokenBalanceResponse = {
  balance: number;
  transactions?: TokenTransaction[] | null;
};

export type TokenHistoryResponse = {
  items: TokenTransaction[];
  total: number;
  skip: number;
  limit: number;
};

export type TokenOperationCost = {
  action: string;
  cost: number;
};

export type TokenOperationCostsResponse = {
  items: TokenOperationCost[];
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
  if (!Number.isInteger(skip) || skip < 0) {
    throw new Error(`Invalid pagination parameter 'skip': expected a non-negative integer, got ${String(skip)}`);
  }
  if (!Number.isInteger(limit) || limit < 0) {
    throw new Error(
      `Invalid pagination parameter 'limit': expected a non-negative integer, got ${String(limit)}`,
    );
  }

  const response = await apiClient.get<TokenHistoryResponse>("/tokens/history", {
    params: { skip, limit },
  });
  return response.data;
}

export async function fetchTokenOperationCosts(): Promise<TokenOperationCost[]> {
  const response = await apiClient.get<TokenOperationCostsResponse>("/tokens/costs");
  return response.data.items ?? [];
}
