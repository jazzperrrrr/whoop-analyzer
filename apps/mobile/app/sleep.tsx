import { useReports } from '../src/api/provider';
import { LoadingState, ErrorState } from '../src/components/ui';
import { SleepScreen } from '../src/features/sleep/SleepScreen';

export default function SleepRoute() {
  const { state, retry } = useReports();
  if (state.status === 'loading') return <LoadingState/>;
  if (state.status === 'error') return <ErrorState retry={retry}/>;
  return <SleepScreen response={state.snapshot.sleep}/>;
}
