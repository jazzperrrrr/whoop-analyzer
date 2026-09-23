import { useReports } from '../src/api/provider';
import { LoadingState, ErrorState } from '../src/components/ui';
import { TodayScreen } from '../src/features/today/TodayScreen';

export default function TodayRoute() {
  const { state, retry } = useReports();
  if (state.status === 'loading') return <LoadingState/>;
  if (state.status === 'error') return <ErrorState retry={retry}/>;
  return <TodayScreen response={state.snapshot.today}/>;
}
