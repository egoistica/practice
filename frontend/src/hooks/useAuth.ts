import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AUTH_TOKEN_STORAGE_KEY, apiClient, setAccessToken } from "../api/client";

const AUTH_REFRESH_TOKEN_STORAGE_KEY = "auth_refresh_token";

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
  refreshToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (payload: LoginPayload) => Promise<TokenResponse>;
  register: (payload: RegisterPayload) => Promise<TokenResponse>;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

export function useAuth(): UseAuthResult {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(AUTH_TOKEN_STORAGE_KEY));
  const [refreshToken, setRefreshToken] = useState<string | null>(() =>
    localStorage.getItem(AUTH_REFRESH_TOKEN_STORAGE_KEY),
  );
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(Boolean(token));

  const applyTokens = useCallback((nextToken: string | null, nextRefreshToken: string | null) => {
    if (nextToken && nextToken.trim()) {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextToken);
      setAccessToken(nextToken);
      setToken(nextToken);
    } else {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
      setAccessToken(null);
      setToken(null);
    }

    if (nextRefreshToken && nextRefreshToken.trim()) {
      localStorage.setItem(AUTH_REFRESH_TOKEN_STORAGE_KEY, nextRefreshToken);
      setRefreshToken(nextRefreshToken);
      return;
    }
    localStorage.removeItem(AUTH_REFRESH_TOKEN_STORAGE_KEY);
    setRefreshToken(null);
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
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const status = error.response?.status;
        if (status === 401 || status === 403) {
          applyTokens(null, null);
          setUser(null);
        }
      }
    } finally {
      setIsLoading(false);
    }
  }, [applyTokens, token]);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setIsLoading(false);
      applyTokens(null, null);
      return;
    }
    setAccessToken(token);
    void refreshUser();
  }, [applyTokens, refreshUser, token]);

  const login = useCallback(
    async (payload: LoginPayload) => {
      const response = await apiClient.post<TokenResponse>("/auth/login", payload);
      applyTokens(response.data.access_token, response.data.refresh_token);
      await refreshUser();
      return response.data;
    },
    [applyTokens, refreshUser],
  );

  const register = useCallback(
    async (payload: RegisterPayload) => {
      const response = await apiClient.post<TokenResponse>("/auth/register", payload);
      applyTokens(response.data.access_token, response.data.refresh_token);
      await refreshUser();
      return response.data;
    },
    [applyTokens, refreshUser],
  );

  const logout = useCallback(() => {
    applyTokens(null, null);
    setUser(null);
    setIsLoading(false);
  }, [applyTokens]);

  const isAuthenticated = useMemo(() => Boolean(token), [token]);

  return {
    user,
    token,
    refreshToken,
    isAuthenticated,
    isLoading,
    login,
    register,
    logout,
    refreshUser,
  };
}
