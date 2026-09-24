import { useState, type PropsWithChildren } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, View, type TextStyle, type StyleProp } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { Metric, Timing } from '../types/api-v1';
import { colors, radius, space, typography } from '../design/tokens';
import { metricText, timeText, type Format } from '../design/format';
import { errorCopy, reasonCopy } from '../design/copy';
import { useDataMode, useReportNotice } from '../api/provider';

export function Copy({ children, kind = 'body', muted = false, style }: PropsWithChildren<{
  kind?: keyof typeof typography; muted?: boolean; style?: StyleProp<TextStyle>;
}>) {
  return <Text style={[{ color: muted ? colors.muted : colors.text }, typography[kind], style]}>{children}</Text>;
}
export function Page({ children }: PropsWithChildren) {
  const notice = useReportNotice();
  return <SafeAreaView style={styles.safe} edges={['left', 'right', 'bottom']}>
    <ScrollView contentContainerStyle={styles.page} showsVerticalScrollIndicator={false}>
      {notice ? <View accessibilityRole="alert" style={styles.section}>
        <Copy>Showing the last loaded report.</Copy><Copy muted>{errorCopy(notice.failure.code)}</Copy>
        {notice.failure.retryable && <Pressable accessibilityRole="button" onPress={notice.retry} style={styles.button}><Copy>Try again</Copy></Pressable>}
      </View> : null}
      {children}
    </ScrollView>
  </SafeAreaView>;
}
export function DataModeBadge() {
  const mode = useDataMode();
  return <Copy kind="caption" muted style={{ letterSpacing: 1 }}>{mode === 'demo' ? 'SAMPLE DATA · DEMO' : mode === 'live-local' || mode === 'native-device' ? 'LOCAL DATA' : 'DATA UNAVAILABLE'}</Copy>;
}
export function Section({ title, children, value }: PropsWithChildren<{ title: string; value?: string }>) {
  return <View testID={`section-${title}`} style={styles.section}>
    <View style={styles.sectionHeading}><Copy kind="section">{title}</Copy>{value ? <Copy kind="section">{value}</Copy> : null}</View>{children}
  </View>;
}
export function MetricValue({ label, metric, format, hero = false, detail = true, compact = false }: {
  label: string; metric: Metric; format: Format; hero?: boolean; detail?: boolean; compact?: boolean;
}) {
  const available = metric.availability === 'available' && metric.value !== null && Number.isFinite(metric.value);
  return <View style={styles.metric} accessible accessibilityLabel={`${label}: ${metricText(metric, format)}`}>
    {!hero && <Copy kind="caption" muted>{label}</Copy>}
    <Copy kind={available ? hero ? 'hero' : compact ? 'section' : 'metric' : 'body'}>{metricText(metric, format)}</Copy>
    {hero && <Copy kind="caption" muted>{label}</Copy>}
    {detail && !available && metric.reason_codes[0] ? <Copy kind="caption" muted>{reasonCopy(metric.reason_codes[0])}</Copy> : null}
  </View>;
}
export function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return <View style={styles.row}>
    <View style={styles.rowLabel}><Copy>{label}</Copy>{note ? <Copy kind="caption" muted>{note}</Copy> : null}</View>
    <Copy style={styles.rowValue}>{value}</Copy>
  </View>;
}
export function SleepTiming({ timing, showOffset = false }: { timing: Timing; showOffset?: boolean }) {
  return <View style={{ gap: space.xs }}>
    <Copy>{timeText(timing.local_start)} → {timeText(timing.local_end)}</Copy>
    {showOffset && <Copy kind="caption" muted>{timing.recorded_offset ? `Recorded time · UTC${timing.recorded_offset === 'Z' ? '+00:00' : timing.recorded_offset}` : 'Recorded time unavailable'}</Copy>}
  </View>;
}
export function QualityNotes({ codes }: { codes: string[] }) {
  const mode = useDataMode();
  const [expanded, setExpanded] = useState(false);
  return <View style={styles.section}>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={() => setExpanded(!expanded)} style={({ pressed }) => [styles.disclosure, { opacity: pressed ? 0.65 : 1 }]}>
      <Copy muted>About these measurements</Copy><Copy muted>{expanded ? '−' : '+'}</Copy>
    </Pressable>
    {expanded ? <View style={{ gap: space.sm }}>
      <Copy kind="caption" muted>{mode === 'demo' ? 'Collection time is unknown. These sample values are for preview only.' : 'Collection time is unknown. This report comes from the local snapshot.'}</Copy>
      {[...new Set(codes.map(reasonCopy))].map(copy => <Copy kind="caption" muted key={copy}>{copy}</Copy>)}
    </View> : null}
  </View>;
}
export function LoadingState() {
  return <Page><DataModeBadge/><View style={styles.status}><ActivityIndicator color={colors.accent}/><Copy>Loading your report…</Copy></View></Page>;
}
export function ErrorState({ retry, code = 'internal_error', retryable = true }: { retry: () => void; code?: string; retryable?: boolean }) {
  return <Page><DataModeBadge/><View style={styles.status}><Copy kind="section">Your report couldn’t be loaded</Copy>
    <Copy muted>{errorCopy(code)}</Copy>
    {retryable && <Pressable accessibilityRole="button" onPress={retry} style={styles.button}><Copy>Try again</Copy></Pressable>}
  </View></Page>;
}
export const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  page: { padding: space.xl, paddingTop: space.lg, paddingBottom: space.xxl, gap: space.xxl, ...(Platform.OS === 'web' ? { maxWidth: 440 } : {}), width: '100%', alignSelf: 'center' },
  section: { gap: space.lg },
  sectionHeading: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'baseline', justifyContent: 'space-between', gap: space.sm },
  // Metrics own only their intrinsic content height. Column sizing belongs to row wrappers.
  metric: { gap: space.xs, minWidth: 0 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.xl },
  gridCell: { width: '45%', flexGrow: 1, gap: space.sm },
  threeColumns: { flexDirection: 'row', gap: space.md, alignItems: 'flex-start' },
  column: { flex: 1, minWidth: 0 },
  card: { backgroundColor: colors.surface, borderRadius: radius.large, padding: space.xl, gap: space.lg },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: space.lg, paddingVertical: space.sm },
  rowLabel: { flex: 1, gap: space.xs },
  rowValue: { textAlign: 'right', flexShrink: 1, fontVariant: ['tabular-nums'] },
  disclosure: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.lg },
  status: { gap: space.xl, paddingVertical: space.huge },
  button: { alignSelf: 'flex-start', padding: space.lg, backgroundColor: colors.accentSoft, borderRadius: radius.small },
});
