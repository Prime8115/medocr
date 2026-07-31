import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/src/auth/AuthContext';
import { Button, Field } from '@/src/theme/components';
import { colors, font, spacing } from '@/src/theme/tokens';
import { t } from '@/src/i18n/strings';

export default function LoginScreen() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [shopName, setShopName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setLoading(true);
    try {
      if (mode === 'login') {
        await signIn(email.trim(), password);
      } else {
        await signUp(email.trim(), password, shopName.trim());
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (typeof detail === 'string') {
        setError(detail); // e.g. "Email already registered", "Incorrect email or password"
      } else if (status === 422) {
        setError('Please check your details — password must be at least 8 characters.');
      } else if (!status) {
        setError('Cannot reach the server. Check your internet connection.');
      } else {
        setError(mode === 'login' ? t('loginError') : t('errorGeneric'));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.container}>
        <Text style={styles.brand}>
          MediScan<Text style={{ color: colors.primary }}>OCR</Text>
        </Text>
        <Text style={styles.subtitle}>
          {mode === 'login' ? t('login') : t('appName')}
        </Text>

        {mode === 'register' && (
          <Field label="Pharmacy name" value={shopName} onChangeText={setShopName} autoCapitalize="words" />
        )}
        <Field
          label={t('email')}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          keyboardType="email-address"
          autoComplete="email"
        />
        <Field
          label={t('password')}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <Button
          title={mode === 'login' ? t('login') : t('appName')}
          onPress={submit}
          loading={loading}
          style={{ marginTop: spacing.md }}
        />

        <View style={styles.switchRow}>
          <Text style={styles.muted}>
            {mode === 'login' ? "New pharmacy? " : 'Have an account? '}
          </Text>
          <Text
            style={styles.link}
            onPress={() => {
              setError(null);
              setMode(mode === 'login' ? 'register' : 'login');
            }}
          >
            {mode === 'login' ? 'Create one' : t('login')}
          </Text>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.bg },
  container: { padding: spacing.xl, paddingTop: spacing.xxl * 2, flexGrow: 1 },
  brand: { ...font.h1, color: colors.text, textAlign: 'center' },
  subtitle: { ...font.body, color: colors.textSecondary, textAlign: 'center', marginBottom: spacing.xl },
  error: { ...font.caption, color: colors.danger, marginTop: spacing.sm },
  switchRow: { flexDirection: 'row', justifyContent: 'center', marginTop: spacing.xl },
  muted: { ...font.body, color: colors.textSecondary },
  link: { ...font.body, color: colors.primary, fontWeight: '600' },
});
