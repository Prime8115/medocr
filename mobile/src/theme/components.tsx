import React from 'react';
import {
  ActivityIndicator,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  TouchableOpacity,
  View,
  ViewStyle,
} from 'react-native';

import { colors, font, radius, spacing, TAP_TARGET } from './tokens';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'success';

export function Button({
  title,
  onPress,
  variant = 'primary',
  loading,
  disabled,
  style,
}: {
  title: string;
  onPress: () => void;
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
}) {
  const bg = {
    primary: colors.primary,
    secondary: colors.surface,
    danger: colors.danger,
    success: colors.success,
  }[variant];
  const fg = variant === 'secondary' ? colors.primary : colors.white;
  const isDisabled = disabled || loading;
  return (
    <TouchableOpacity
      accessibilityRole="button"
      onPress={onPress}
      disabled={isDisabled}
      activeOpacity={0.8}
      style={[
        styles.button,
        { backgroundColor: bg, opacity: isDisabled ? 0.6 : 1 },
        variant === 'secondary' && styles.buttonOutline,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <Text style={[styles.buttonText, { color: fg }]}>{title}</Text>
      )}
    </TouchableOpacity>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

export function Field({
  label,
  value,
  onChangeText,
  accentColor,
  hint,
  ...rest
}: {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  accentColor?: string;
  hint?: string;
} & TextInputProps) {
  return (
    <View style={styles.fieldWrap}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        style={[styles.input, accentColor ? { borderColor: accentColor, borderLeftWidth: 4 } : null]}
        value={value}
        onChangeText={onChangeText}
        placeholderTextColor={colors.textMuted}
        {...rest}
      />
      {hint ? <Text style={styles.fieldHint}>{hint}</Text> : null}
    </View>
  );
}

export function Badge({ label, tone = 'neutral' }: { label: string; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }) {
  const map = {
    neutral: { bg: colors.surfaceAlt, fg: colors.textSecondary },
    success: { bg: colors.successTint, fg: colors.success },
    warning: { bg: colors.warningTint, fg: colors.warning },
    danger: { bg: colors.dangerTint, fg: colors.danger },
    info: { bg: colors.primaryTint, fg: colors.primaryDark },
  }[tone];
  return (
    <View style={[styles.badge, { backgroundColor: map.bg }]}>
      <Text style={[styles.badgeText, { color: map.fg }]}>{label}</Text>
    </View>
  );
}

export function Screen({ children, style }: { children?: React.ReactNode; style?: ViewStyle }) {
  return <View style={[styles.screen, style]}>{children}</View>;
}

export function CenterState({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <View style={styles.center}>
      <Text style={styles.centerTitle}>{title}</Text>
      {subtitle ? <Text style={styles.centerSubtitle}>{subtitle}</Text> : null}
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  button: {
    minHeight: TAP_TARGET,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
  },
  buttonOutline: { borderWidth: 1, borderColor: colors.border },
  buttonText: { ...font.h3 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    padding: spacing.lg,
    marginBottom: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  sectionTitle: { ...font.label, color: colors.textSecondary, textTransform: 'uppercase', marginBottom: spacing.sm },
  fieldWrap: { marginBottom: spacing.md },
  fieldLabel: { ...font.label, color: colors.textSecondary, marginBottom: spacing.xs },
  input: {
    minHeight: TAP_TARGET,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    ...font.body,
    color: colors.text,
    backgroundColor: colors.surface,
  },
  fieldHint: { ...font.caption, color: colors.danger, marginTop: spacing.xs },
  badge: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radius.pill, alignSelf: 'flex-start' },
  badgeText: { ...font.caption, fontWeight: '600' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  centerTitle: { ...font.h2, color: colors.text, textAlign: 'center' },
  centerSubtitle: { ...font.body, color: colors.textSecondary, textAlign: 'center', marginTop: spacing.sm },
});
