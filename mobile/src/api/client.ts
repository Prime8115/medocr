import axios from 'axios';
import { API_URL } from '../config';

/** Axios instance pointed at the configured backend. */
export const api = axios.create({ baseURL: API_URL, timeout: 30000 });

let tokenGetter: () => string | null = () => null;

/** Register how the client obtains the current auth token (set by AuthContext). */
export function setTokenGetter(fn: () => string | null) {
  tokenGetter = fn;
}

api.interceptors.request.use((config) => {
  const token = tokenGetter();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
