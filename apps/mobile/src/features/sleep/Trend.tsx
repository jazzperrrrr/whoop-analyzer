import { View } from 'react-native';
import type { TrendReport } from '../../types/api-v1';
import { Copy, Row, styles } from '../../components/ui';
import { dateText, metricText, numberText } from '../../design/format';
import { colors, radius, space } from '../../design/tokens';

export function Trend({ trend }: { trend: TrendReport }) {
  const available = trend.daily_points.flatMap(p => p.metric.availability === 'available' && p.metric.value !== null && Number.isFinite(p.metric.value) ? [p.metric.value] : []);
  const max = Math.max(1, ...available); // Visual scale only; never a health comparison.
  return <View style={{ gap: space.md }}>
    <Copy muted>Actual sleep · recorded nights</Copy>
    <Copy kind="caption" muted>Only recorded dates are shown. Gaps are not treated as zero sleep.</Copy>
    {trend.daily_points.length === 0 ? <Copy>No recorded nights yet.</Copy> : trend.daily_points.map((point, index) => {
      const value = point.metric.value;
      const numeric = point.metric.availability === 'available' && value !== null && Number.isFinite(value);
      const previous = trend.daily_points[index - 1];
      // Calendar spacing is presentation only; never fabricate observations for absent dates.
      const gap = previous && Date.parse(point.report_date) - Date.parse(previous.report_date) > 86_400_000;
      return <View key={point.report_date} style={{ gap: space.xs }}>
        {gap ? <Copy kind="caption" muted>··· No observations between these dates</Copy> : null}
        <View accessible accessibilityLabel={`${dateText(point.report_date)}: ${metricText(point.metric, 'duration')}`}>
        <Row label={dateText(point.report_date, true)} value={metricText(point.metric, 'duration')}/>
        {numeric ? <View style={{ height: 8, borderRadius: radius.small, backgroundColor: colors.line }}>
          <View testID={`trend-bar-${point.report_date}`} style={{ width: `${Math.max(0, Math.min(100, value / max * 100))}%`, height: 8, borderRadius: radius.small, backgroundColor: colors.accent }}/>
        </View> : <Copy kind="caption" muted>No bar for this night</Copy>}
        </View>
      </View>;
    })}
    <View style={{ gap: space.md, paddingTop: space.lg }}>
      <Copy kind="caption" muted>Previous averages · excludes selected night</Copy>
      <View style={styles.threeColumns}>{trend.prior_windows.map(window => <View key={window.window_days} style={[styles.column, { gap: space.xs }]}>
        <Copy kind="caption" muted>{window.window_days} days</Copy>
        <Copy>{window.average === null ? 'Not available' : numberText(window.average, 'duration')}</Copy>
        <Copy kind="caption" muted>{window.observed_days} recorded</Copy>
      </View>)}</View>
    </View>
  </View>;
}
