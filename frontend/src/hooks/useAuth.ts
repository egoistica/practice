import { useCallback, useEffect, useMemo, useState } from "react";

import { AUTH_TOKEN_STORAGE_KEY, apiClient, setAccessToken } from "../api/client";

type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: string;
};

type AuthUser = {
  user_id: string;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
};

type LoginPayload = {
  username: string;
  password: string;
};

type RegisterPayload = {
  username: string;
  email: string;
  password: string;
};

export type UseAuthResult = {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (payload: LoginPayload) => Promise<TokenResponse>;
  register: (payload: RegisterPayload) => Promise<TokenResponse>;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

export function useAuth(): UseAuthResult {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(AUTH_TOKEN_STORAGE_KEY));
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(Boolean(token));

  const applyToken = useCallback((nextToken: string | null) => {
    if (nextToken) {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextToken);
      setAccessToken(nextToken);
      setToken(nextToken);
      return;
    }
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    setAccessToken(null);
    setToken(null);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!token) {
      setUser(null);
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const response = await apiClient.get<AuthUser>("/auth/me");
      setUser(response.data);
    } catch (_error) {
      applyToken(null);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, [applyToken, token]);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setIsLoading(false);
      setAccessToken(null);
      return;
    }
    setAccessToken(token);
    void refreshUser();
  }, [refreshUser, token]);

  const login = useCallback(
    async (payload: LoginPayload) => {
      const response = await apiClient.post<TokenResponse>("/auth/login", payload);
      applyToken(response.data.access_token);
      await refreshUser();
      return response.data;
    },
    [applyToken, refreshUser],
  );

  const register = useCallback(
    async (payload: RegisterPayload) => {
      const response = await apiClient.post<TokenResponse>("/auth/register", payload);
      applyToken(response.data.access_token);
      await refreshUser();
      return response.data;
    },
    [applyToken, refreshUser],
  );

  const logout = useCallback(() => {
    applyToken(null);
    setUser(null);
    setIsLoading(false);
  }, [applyToken]);

  const isAuthenticated = useMemo(() => Boolean(token), [token]);

  return {
    user,
    token,
    isAuthenticated,
    isLoading,
    login,
    register,
    logout,
    refreshUser,
  };
}

