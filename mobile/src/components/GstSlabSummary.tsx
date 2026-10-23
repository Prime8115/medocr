import { useWindowDimensions, StyleSheet, Text, View } from 'react-native';

import { GstSlabRow, gstSlabRate } from '@/src/lib/gstSlabs';
import { formatMoney } from '@/src/lib/table';
import { colors, font, radius, spacing } from '@/src/theme/tokens';

function figure(label: string, value?: string) {
  return value == null ? null : `${label} ₹${formatMoney(value)}`;
}

function tax(label: string, percent?: string, amount?: string) {
  if (amount == null) return null;
  return `${label}${percent ? ` ${Number(percent)}%` : ''} ₹${formatMoney(amount)}`;
}

export default function GstSlabSummary({ rows }: { rows: GstSlabRow[] }) {
  const { width } = useWindowDimensions();
  if (rows.length === 0) return null;
  return (
    <View style={[styles.container, { width: Math.max(280, Math.min(width - spacing.md * 2, 520)) }]}>
      <Text style={styles.title}>GST breakdown · as printed</Text>
      {rows.map((row, index) => {
        const rate = gstSlabRate(row);
        const base = [figure('Amount', row.amount), figure('Scheme', row.scheme_amount),
          figure('Discount', row.discount_amount)].filter(Boolean).join('  ·  ');
        const taxes = [tax('CGST', row.cgst_percent, row.cgst_amount),
          tax('SGST', row.sgst_percent, row.sgst_amount),
          tax('IGST', row.igst_percent, row.igst_amount),
          tax('UTGST', row.utgst_percent, row.utgst_amount)].filter(Boolean).join('  ·  ');
        return (
          <View key={index} style={styles.slab}>
            <View style={styles.heading}>
              <Text style={styles.rate}>{rate ? `GST ${rate}` : `GST division ${index + 1}`}</Text>
              {row.total_amount ? <Text style={styles.total}>₹{formatMoney(row.total_amount)} total</Text> : null}
            </View>
            {base ? <Text style={styles.detail}>{base}</Text> : null}
            {row.taxable_amount ? <Text style={styles.detail}>{figure('Taxable', row.taxable_amount)}</Text> : null}
            {taxes ? <Text style={styles.detail}>{taxes}</Text> : null}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { margin: spacing.md, padding: spacing.md, backgroundColor: colors.surface,
    borderColor: colors.border, borderWidth: 1, borderRadius: radius.md },
  title: { ...font.label, color: colors.text, marginBottom: spacing.sm },
  slab: { paddingVertical: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border },
  heading: { flexDirection: 'row', justifyContent: 'space-between', flexWrap: 'wrap', gap: spacing.xs },
  rate: { ...font.label, color: colors.primaryDark },
  total: { ...font.label, color: colors.text },
  detail: { ...font.caption, color: colors.textSecondary, lineHeight: 18, marginTop: spacing.xs },
});
