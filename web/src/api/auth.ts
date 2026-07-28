import { api } from './client';

export interface User {
  id: string;
  email: string;
  role: string;
  shop_id: string;
}

export async function login(email: string, password: string): Promise<string> {
  const form = new URLSearchParams();
  form.append('username', email);
  form.append('password', password);
  const res = await api.post('/v1/auth/login', form.toString(), {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  return res.data.access_token as string;
}

export async function me(): Promise<User> {
  const res = await api.get('/v1/auth/me');
  return res.data as User;
}
