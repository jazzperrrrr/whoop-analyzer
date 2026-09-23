import { Link } from 'expo-router';
import { Pressable, View } from 'react-native';
import type { TodayResponse, TodayPhysiology } from '../../types/api-v1';
import { Copy, DemoBadge, MetricValue, Page, QualityNotes, SleepTiming, styles } from '../../components/ui';
import { dateText, metricText, type Format } from '../../design/format';
import { overallCopy, reasonCopy } from '../../design/copy';
import { colors, space } from '../../design/tokens';

const measurements: Array<{ key: keyof TodayPhysiology; label: string; format: Format }> = [
  { key: 'recovery_score', label: 'Recovery', format: 'percent' },
  { key: 'hrv_ms', label: 'HRV', format: 'hrv' },
  { key: 'resting_heart_rate_bpm', label: 'Resting HR', format: 'bpm' },
  { key: 'sleep_performance', label: 'Sleep Performance', format: 'percent' },
];
export function TodayScreen({ response }: { response: TodayResponse }) {
  const { physiology, interpretation, last_sleep: sleep } = response.data;
  return <Page>
    <View style={{ gap: space.sm }}><Copy kind="caption" muted>{dateText(response.report_date)}</Copy><Copy kind="title">Good morning</Copy><DemoBadge/></View>
    <View style={styles.grid}>
      {measurements.map(({ key, label, format }) => <View key={key} style={styles.gridCell}>
        <MetricValue label={label} metric={physiology[key].metric} format={format}/>
        <Copy kind="caption" muted>{interpretation.signals[key].reason_codes.map(reasonCopy).join(' ') || 'Compared with your recent measurements.'}</Copy>
      </View>)}
    </View>
    <View style={{ gap: space.sm }}>
      <Copy kind="section">{overallCopy(interpretation.overall_state)}</Copy>
      <Copy muted>{[...new Set(interpretation.reason_codes.map(reasonCopy))].join(' ') || 'Compared with your recent measurements.'}</Copy>
    </View>
    <Link href="/sleep" asChild>
      <Pressable accessibilityRole="link" accessibilityLabel="Last Sleep, view sleep details" style={({ pressed }) => [styles.card, { opacity: pressed ? 0.75 : 1 }]}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: space.lg }}><Copy kind="section">Last Sleep</Copy><Copy style={{ color: colors.accent }}>↗</Copy></View>
        <MetricValue label="Actual Sleep" metric={sleep.actual_sleep} format="duration" hero/>
        <Copy muted>{metricText(sleep.sleep_need, 'duration')} estimated need</Copy>
        <View style={styles.threeColumns}>
          <View style={styles.column}><MetricValue label="Performance" metric={sleep.sleep_performance} format="percent" compact/></View>
          <View style={styles.column}><MetricValue label="Efficiency" metric={sleep.sleep_efficiency} format="percent" compact/></View>
        </View>
        <SleepTiming timing={sleep.timing}/>
      </Pressable>
    </Link>
    <QualityNotes codes={response.data.quality_codes}/>
  </Page>;
}
