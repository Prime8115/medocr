/**
 * Invoice line items as a table — the view a pharmacist already has on paper.
 *
 * A 143-line invoice cannot be checked as 143 cards: you cannot compare rows,
 * scan a column of numbers, or spot a repeat. A table can do all three, so it is
 * the default for invoices.
 *
 * Layout notes:
 *  - The header sits inside the same horizontal scroller as the rows, above the
 *    list, so it stays put vertically and tracks the rows horizontally.
 *  - Columns are ordered so Item / Qty / Rate / Amount fit on a phone without
 *    scrolling sideways; batch, expiry and the rest are to the right.
 *  - Numbers are right-aligned with fixed decimals and fixed column widths, so
 *    an odd row is visible as a shape, not just as a value.
 *  - A tap opens the existing edit sheet for that line.
 */
import { memo } from 'react';
import { FlatList, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { Fields, Leaf } from '@/src/lib/payload';
import { Column, cellText, invoiceColumns, lineItems, totalWidth } from '@/src/lib/table';
import { isLowConfidence } from '@/src/lib/confidence';
import { colors, font, radius, spacing } from '@/src/theme/tokens';

export interface TableRow {
  item: Record<string, Leaf>;
  /** Index into the untouched line_items array — what the edit sheet needs. */
  index: number;
  attention: boolean;
}

function HeaderCell({ column }: { column: Column }) {
  return (
    <View style={[styles.cell, { width: column.width }]}>
      <Text
        style={[styles.headerText, column.numeric && styles.right]}
        numberOfLines={1}
      >
        {column.label}
      </Text>
      {column.sub ? (
        <Text style={[styles.headerSub, column.numeric && styles.right]} numberOfLines={1}>
          ({column.sub})
        </Text>
      ) : null}
    </View>
  );
}

const Row = memo(function Row({
  row,
  columns,
  width,
  onPress,
}: {
  row: TableRow;
  columns: Column[];
  width: number;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      onPress={onPress}
      activeOpacity={0.6}
      accessibilityRole="button"
      accessibilityLabel={`Line ${row.index + 1}: ${row.item?.description?.value ?? 'unnamed'}`}
      style={[styles.row, { width }, row.attention && styles.rowAttention]}
    >
      {columns.map((column) => {
        const leaf = row.item?.[column.key];
        const low = isLowConfidence(leaf?.confidence ?? null);
        return (
          <View key={column.key} style={[styles.cell, { width: column.width }, low && styles.cellLow]}>
            <Text
              style={[
                styles.cellText,
                column.numeric && styles.right,
                column.key === 'description' && styles.cellPrimary,
              ]}
              numberOfLines={2}
            >
              {cellText(row.item, column) || '—'}
            </Text>
          </View>
        );
      })}
    </TouchableOpacity>
  );
});

export default function InvoiceTable({
  fields,
  rows,
  onSelect,
  listHeader,
  emptyText,
}: {
  fields: Fields;
  rows: TableRow[];
  onSelect: (index: number) => void;
  listHeader?: React.ReactElement;
  emptyText?: string;
}) {
  const columns = invoiceColumns(fields);
  const width = totalWidth(columns);

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator contentContainerStyle={{ minWidth: '100%' }}>
      <View style={{ width }}>
        {listHeader}
        <View style={[styles.headerRow, { width }]}>
          {columns.map((column) => (
            <HeaderCell key={column.key} column={column} />
          ))}
        </View>
        <FlatList
          data={rows}
          keyExtractor={(row) => String(row.index)}
          initialNumToRender={20}
          windowSize={11}
          removeClippedSubviews
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={emptyText ? <Text style={styles.empty}>{emptyText}</Text> : null}
          renderItem={({ item: row }) => (
            <Row row={row} columns={columns} width={width} onPress={() => onSelect(row.index)} />
          )}
        />
      </View>
    </ScrollView>
  );
}

/** Column count is derived, so expose it for callers that show a hint. */
export function columnCount(fields: Fields): number {
  return invoiceColumns(fields).length;
}

export { lineItems };

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceAlt,
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: colors.border,
    paddingVertical: spacing.sm,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 46,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  rowAttention: { backgroundColor: colors.warningTint },
  cell: { paddingHorizontal: spacing.sm, justifyContent: 'center' },
  cellLow: { backgroundColor: colors.dangerTint },
  headerText: { ...font.caption, color: colors.textSecondary, fontWeight: '700', textTransform: 'uppercase' },
  headerSub: { ...font.caption, fontSize: 10, color: colors.textMuted },
  cellText: { ...font.caption, fontSize: 13, color: colors.text },
  cellPrimary: { fontWeight: '600' },
  right: { textAlign: 'right' },
  empty: { ...font.body, color: colors.textMuted, textAlign: 'center', padding: spacing.xl },
});
