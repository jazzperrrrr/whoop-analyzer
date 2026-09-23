import { View } from 'react-native';
import type { SleepResponse, SleepMetrics } from '../../types/api-v1';
import { Copy, DataModeBadge, MetricValue, Page, QualityNotes, Row, Section, SleepTiming, styles } from '../../components/ui';
import { dateText, metricText, needDifferenceText } from '../../design/format';
import { colors, radius, space } from '../../design/tokens';
import { Trend } from './Trend';

const stages: Array<{ label: string; duration: keyof SleepMetrics; percentage: keyof SleepMetrics; color: string }> = [
  { label: 'Light', duration: 'light_ms', percentage: 'light_pct_actual_sleep', color: colors.lightStage },
  { label: 'Deep', duration: 'deep_ms', percentage: 'deep_pct_actual_sleep', color: colors.deepStage },
  { label: 'REM', duration: 'rem_ms', percentage: 'rem_pct_actual_sleep', color: colors.remStage },
];
const need: Array<[string, keyof SleepMetrics]> = [
  ['Baseline', 'baseline_need_ms'], ['Sleep debt', 'sleep_debt_need_ms'],
  ['Recent strain', 'recent_strain_need_ms'], ['Nap adjustment', 'nap_adjustment_ms'],
];
export function SleepScreen({ response }: { response: SleepResponse }) {
  const { metrics: m, timing, trends, quality_codes } = response.data;
  const completeStages = stages.every(s => m[s.percentage].availability === 'available' && m[s.percentage].value !== null && Number.isFinite(m[s.percentage].value));
  return <Page>
    <View testID="sleep-hero" style={{ gap: space.xl }}>
      <View style={{ gap: space.sm }}><Copy kind="caption" muted>{dateText(response.report_date)}</Copy><Copy kind="title">Your night</Copy><DataModeBadge/></View>
      <MetricValue label="Actual Sleep" metric={m.actual_sleep_ms} format="duration" hero/>
      <View style={{ gap: space.xs }}><SleepTiming timing={timing}/><Copy muted>{metricText(m.total_need_ms, 'duration')} estimated need</Copy></View>
      <View testID="sleep-scores" style={styles.threeColumns}>
        <View style={styles.column}><MetricValue label="Performance" metric={m.performance_pct} format="percent"/></View>
        <View style={styles.column}><MetricValue label="Efficiency" metric={m.efficiency_pct} format="percent"/></View>
        <View style={styles.column}><MetricValue label="Consistency" metric={m.consistency_pct} format="percent"/></View>
      </View>
    </View>
    <Section title="Sleep Stages">
      {completeStages ? <View accessibilityLabel="Light, deep and REM composition of actual sleep" style={{ flexDirection: 'row', height: 24, overflow: 'hidden', borderRadius: radius.small, backgroundColor: colors.line }}>
        {stages.map(s => <View key={s.label} style={{ width: `${Math.max(0, Math.min(100, m[s.percentage].value!))}%`, backgroundColor: s.color }}/>) }
      </View> : <Copy muted>Stage composition is not available.</Copy>}
      <View style={{ gap: space.xs }}>{stages.map(s => <Row key={s.label} label={s.label} value={`${metricText(m[s.duration], 'duration')} · ${metricText(m[s.percentage], 'percent')}`}/>)}</View>
      <View style={{ borderTopWidth: 1, borderTopColor: colors.line, paddingTop: space.sm }}>
        <Row label="Restorative" value={`${metricText(m.restorative_sleep_ms, 'duration')} · ${metricText(m.restorative_pct_actual_sleep, 'percent')}`} note="Deep + REM"/>
        <Row label="Awake" value={`${metricText(m.awake_ms, 'duration')} · ${metricText(m.awake_pct_in_bed, 'percent')}`} note="% of time in bed"/>
      </View>
      <Copy kind="caption" muted>Composition totals, not a timeline. Restorative sleep includes deep and REM.</Copy>
    </Section>
    <Section title="Sleep Need" value={metricText(m.total_need_ms, 'duration')}>
      <View style={{ gap: space.md }}>{need.map(([label, key]) => <View key={key} style={{ flexDirection: 'row', alignItems: 'baseline', gap: space.lg }}>
        <View style={{ width: '34%' }}><Copy>{metricText(m[key], key === 'baseline_need_ms' ? 'duration' : 'signedDuration')}</Copy></View><View style={styles.column}><Copy muted>{label}</Copy></View>
      </View>)}</View>
      <View style={{ backgroundColor: colors.accentSoft, borderRadius: radius.medium, padding: space.lg }}><Copy>{needDifferenceText(m.actual_minus_need_ms)}</Copy></View>
    </Section>
    <Section title="Quality">
      <View style={styles.threeColumns}>
        <View style={styles.column}><MetricValue label="Respiratory Rate" metric={m.respiratory_rate} format="breaths" compact/></View>
        <View style={styles.column}><MetricValue label="Cycles" metric={m.sleep_cycle_count} format="count" compact/></View>
        <View style={styles.column}><MetricValue label="Disturbances" metric={m.disturbance_count} format="count" compact/></View>
      </View>
    </Section>
    <Section title="Recent sleep"><Trend trend={trends.actual_sleep}/></Section>
    <QualityNotes codes={quality_codes}/>
  </Page>;
}
