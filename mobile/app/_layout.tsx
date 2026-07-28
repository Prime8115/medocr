import { Stack, useRouter, useSegments } from 'expo-router';
import { useEffect } from 'react';
import { View } from 'react-native';

import { AuthProvider, useAuth } from '@/src/auth/AuthContext';
import { QueueProvider } from '@/src/queue/QueueContext';
import { colors } from '@/src/theme/tokens';

export { ErrorBoundary } from 'expo-router';

export const unstable_settings = {
  initialRouteName: '(tabs)',
};

function useProtectedRoute(user: unknown, ready: boolean) {
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    const inAuthGroup = segments[0] === 'login';
    if (!user && !inAuthGroup) {
      router.replace('/login');
    } else if (user && inAuthGroup) {
      router.replace('/(tabs)/capture');
    }
  }, [user, ready, segments, router]);
}

function RootNav() {
  const { user, loading } = useAuth();
  useProtectedRoute(user, !loading);

  if (loading) {
    return <View style={{ flex: 1, backgroundColor: colors.bg }} />;
  }

  return (
    <Stack screenOptions={{ headerTintColor: colors.text }}>
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="login" options={{ headerShown: false }} />
      <Stack.Screen name="review/[id]" options={{ title: 'Review' }} />
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <QueueProvider>
        <RootNav />
      </QueueProvider>
    </AuthProvider>
  );
}
