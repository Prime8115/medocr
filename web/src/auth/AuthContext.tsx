import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { login as apiLogin, me as apiMe, register as apiRegister, type User } from '../api/auth';
import { getToken, setToken } from '../api/client';

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, shopName: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      if (getToken()) {
        try {
          setUser(await apiMe());
        } catch {
          setToken(null);
        }
      }
      setLoading(false);
    })();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      async signIn(email, password) {
        setToken(await apiLogin(email, password));
        setUser(await apiMe());
      },
      async signUp(email, password, shopName) {
        await apiRegister(email, password, shopName);
        setToken(await apiLogin(email, password));
        setUser(await apiMe());
      },
      signOut() {
        setToken(null);
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
