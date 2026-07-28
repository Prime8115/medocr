import axios from 'axios';

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8080';

export const api = axios.create({ baseURL: API_URL, timeout: 30000 });

const TOKEN_KEY = 'mediscan_admin_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
