import { api, setToken } from './client';

export interface User {
  id: string;
  email: string;
  role: string;
  shop_id: string;
  is_platform_admin: boolean;
  /** The organisation, the workspace the user is in, and the ones they may open. */
  org_id?: string | null;
  org_name?: string | null;
  workspace_name?: string | null;
  workspaces?: { id: string; name: string }[];
}

/** Select another workspace in this browser session. */
export async function switchWorkspace(workspaceId: string): Promise<User> {
  const res = await api.post('/v1/auth/workspace', { workspace_id: workspaceId });
  const session = res.data as { access_token: string; user: User };
  setToken(session.access_token);
  return session.user;
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
  /** The owner's consent for corrections to improve the reader for every shop. */
  share_corrections?: boolean;
}

export async function getShop(): Promise<ShopProfile> {
  const res = await api.get('/v1/auth/shop');
  return res.data as ShopProfile;
}

export async function setShopGstins(gstins: string[]): Promise<string[]> {
  const res = await api.put('/v1/auth/shop/gstins', { gstins });
  return (res.data as { gstins: string[] }).gstins;
}

export async function setShareCorrections(share: boolean): Promise<boolean> {
  const res = await api.put('/v1/auth/shop/share-corrections', { share });
  return (res.data as { share_corrections: boolean }).share_corrections;
}
