import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import * as SecureStore from 'expo-secure-store';

import { setTokenGetter } from '../api/client';
import { login as apiLogin, me as apiMe, register as apiRegister, User } from '../api/auth';

const TOKEN_KEY = 'mediscan_token';

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, shopName: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const tokenRef = useRef<string | null>(null);

  // Let the API client read the current token synchronously.
  useEffect(() => {
    setTokenGetter(() => tokenRef.current);
  }, []);

  // Restore a saved session on launch.
  useEffect(() => {
    (async () => {
      try {
        const saved = await SecureStore.getItemAsync(TOKEN_KEY);
        if (saved) {
          tokenRef.current = saved;
          setUser(await apiMe());
        }
      } catch {
        tokenRef.current = null;
        await SecureStore.deleteItemAsync(TOKEN_KEY);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function persistToken(token: string) {
    tokenRef.current = token;
    await SecureStore.setItemAsync(TOKEN_KEY, token);
    setUser(await apiMe());
  }

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      async signIn(email, password) {
        await persistToken(await apiLogin(email, password));
      },
      async signUp(email, password, shopName) {
        await apiRegister(email, password, shopName);
        await persistToken(await apiLogin(email, password));
      },
      async signOut() {
        tokenRef.current = null;
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        setUser(null);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
