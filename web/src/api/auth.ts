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

export async function register(email: string, password: string, shopName: string): Promise<User> {
  const res = await api.post('/v1/auth/register', { email, password, shop_name: shopName });
  return res.data as User;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await api.post('/v1/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

/** The shop and the GSTINs it is known by (GET /v1/auth/shop). */
export interface ShopProfile {
  name: string | null;
  /** Stated by the owner, then learned from approved bills. */
  gstins: string[];
  stated: string[];
}

export async function getShop(): Promise<ShopProfile> {
  const res = await api.get('/v1/auth/shop');
  return res.data as ShopProfile;
}

export async function setShopGstins(gstins: string[]): Promise<string[]> {
  const res = await api.put('/v1/auth/shop/gstins', { gstins });
  return (res.data as { gstins: string[] }).gstins;
}
