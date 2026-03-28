import axios from "axios";

export const AUTH_TOKEN_STORAGE_KEY = "auth_token";
export const AUTH_REFRESH_TOKEN_STORAGE_KEY = "auth_refresh_token";
export const AUTH_STATE_CHANGED_EVENT = "auth-state-changed";

const configuredBaseURL = String(import.meta.env.VITE_API_BASE_URL || "").trim();
const isLocalDevHost = typeof window !== "undefined"
  && ["localhost", "127.0.0.1"].includes(window.location.hostname);
const configuredPointsToLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(configuredBaseURL);
const shouldBypassConfiguredBase = Boolean(configuredBaseURL)
  && configuredPointsToLocalhost
  && !isLocalDevHost;
const baseURL = shouldBypassConfiguredBase
  ? "/api"
  : (configuredBaseURL || (isLocalDevHost ? "http://localhost:8000" : "/api"));

export const apiClient = axios.create({
  baseURL,
});

type RefreshTokenResponse = {
  access_token: string;
  refresh_token?: string;
};

type RetryableRequestConfig = {
  _retry?: boolean;
};

let refreshInFlight: Promise<string | null> | null = null;

function emitAuthStateChanged(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new Event(AUTH_STATE_CHANGED_EVENT));
}

export function setAccessToken(token: string | null): void {
  if (token && token.trim()) {
    apiClient.defaults.headers.common.Authorization = `Bearer ${token.trim()}`;
    return;
  }
  delete apiClient.defaults.headers.common.Authorization;
}

function clearStoredAuth(): void {
  localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  localStorage.removeItem(AUTH_REFRESH_TOKEN_STORAGE_KEY);
  setAccessToken(null);
  emitAuthStateChanged();
}

function isAuthEndpoint(url?: string): boolean {
  const normalized = String(url || "").toLowerCase();
  return (
    normalized.includes("/auth/login")
    || normalized.includes("/auth/register")
    || normalized.includes("/auth/refresh")
  );
}

async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  const refreshToken = localStorage.getItem(AUTH_REFRESH_TOKEN_STORAGE_KEY)?.trim();
  if (!refreshToken) {
    clearStoredAuth();
    return null;
  }

  refreshInFlight = (async () => {
    try {
      const response = await axios.post<RefreshTokenResponse>(`${baseURL}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      const nextAccessToken = String(response.data.access_token || "").trim();
      const nextRefreshToken = String(response.data.refresh_token || refreshToken).trim();

      if (!nextAccessToken) {
        clearStoredAuth();
        return null;
      }

      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, nextAccessToken);
      if (nextRefreshToken) {
        localStorage.setItem(AUTH_REFRESH_TOKEN_STORAGE_KEY, nextRefreshToken);
      } else {
        localStorage.removeItem(AUTH_REFRESH_TOKEN_STORAGE_KEY);
      }
      setAccessToken(nextAccessToken);
      emitAuthStateChanged();
      return nextAccessToken;
    } catch {
      clearStoredAuth();
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  if (token && token.trim()) {
    config.headers.Authorization = `Bearer ${token.trim()}`;
  }
  if (typeof FormData !== "undefined" && config.data instanceof FormData) {
    if (typeof config.headers.delete === "function") {
      config.headers.delete("Content-Type");
      config.headers.delete("content-type");
    }
    delete config.headers["Content-Type"];
    delete config.headers["content-type"];
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401 && error.config) {
      const requestConfig = error.config as typeof error.config & RetryableRequestConfig;
      if (!requestConfig._retry && !isAuthEndpoint(requestConfig.url)) {
        requestConfig._retry = true;
        const nextAccessToken = await refreshAccessToken();
        if (nextAccessToken) {
          requestConfig.headers = requestConfig.headers || {};
          requestConfig.headers.Authorization = `Bearer ${nextAccessToken}`;
          return apiClient(requestConfig);
        }
      }
    }

    if (axios.isAxiosError(error) && error.response?.status === 429) {
      const retryAfter = error.response.headers?.["retry-after"];
      const currentData = error.response.data;
      const currentDetail =
        currentData && typeof currentData === "object" && "detail" in currentData
          ? String((currentData as { detail?: unknown }).detail ?? "").trim()
          : "";

      let detail = currentDetail || "Слишком много запросов. Повторите попытку позже.";
      if (retryAfter && /^\d+$/.test(String(retryAfter))) {
        detail = `${detail} Повторите через ${String(retryAfter)} сек.`;
      }

      if (!currentData || typeof currentData !== "object") {
        error.response.data = { detail };
      } else {
        (currentData as Record<string, unknown>).detail = detail;
      }
    }
    return Promise.reject(error);
  },
);
