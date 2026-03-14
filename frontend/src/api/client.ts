import axios from "axios";

export const AUTH_TOKEN_STORAGE_KEY = "auth_token";

const baseURL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export const apiClient = axios.create({
  baseURL,
  headers: {
    "Content-Type": "application/json",
  },
});

export function setAccessToken(token: string | null): void {
  if (token && token.trim()) {
    apiClient.defaults.headers.common.Authorization = `Bearer ${token.trim()}`;
    return;
  }
  delete apiClient.defaults.headers.common.Authorization;
}

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  if (token && token.trim()) {
    config.headers.Authorization = `Bearer ${token.trim()}`;
  }
  return config;
});

