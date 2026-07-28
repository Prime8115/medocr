/** Design tokens — the single source of truth for the mobile UI. */

export const colors = {
  primary: '#0B7DDA',
  primaryDark: '#0A5FA6',
  primaryTint: '#E6F2FB',

  bg: '#F4F6F8',
  surface: '#FFFFFF',
  surfaceAlt: '#F0F3F6',

  text: '#132030',
  textSecondary: '#5B6B7B',
  textMuted: '#8A98A6',

  border: '#DCE3EA',

  success: '#1E9E5A',
  successTint: '#E4F5EC',
  warning: '#C9821A',
  warningTint: '#FBF0DE',
  danger: '#D4453B',
  dangerTint: '#FBE7E5',

  white: '#FFFFFF',
  overlay: 'rgba(9, 20, 33, 0.55)',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  pill: 999,
};

export const font = {
  h1: { fontSize: 26, fontWeight: '700' as const },
  h2: { fontSize: 20, fontWeight: '700' as const },
  h3: { fontSize: 17, fontWeight: '600' as const },
  body: { fontSize: 16, fontWeight: '400' as const },
  label: { fontSize: 13, fontWeight: '600' as const },
  caption: { fontSize: 12, fontWeight: '400' as const },
};

/** Minimum touch target for counter staff (accessibility). */
export const TAP_TARGET = 48;
