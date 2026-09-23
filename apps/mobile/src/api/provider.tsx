import { createContext, useContext, useEffect, useState, type PropsWithChildren } from 'react';
import type { ApiClient } from './client';
import { createApiClient, publicDataConfig, type DataConfig, type DataMode } from './mode';
import { safeFailure, type ApiFailure } from './errors';
import { loadSnapshot, MemorySnapshotCache, type CachedSnapshot } from '../storage/snapshot';

type State = { status: 'loading' } | { status: 'ready'; snapshot: CachedSnapshot; notice?: ApiFailure } | { status: 'error'; failure: ApiFailure };
const Context = createContext<{ state: State; retry: () => void } | null>(null);
const ModeContext = createContext<DataMode | 'unavailable'>('demo');
export function ApiProvider({ children, client, config }: PropsWithChildren<{ client?: ApiClient; config?: DataConfig }>) {
  const [runtime] = useState(() => {
    const configured = config ?? publicDataConfig();
    try {
      const source = client ?? createApiClient(configured);
      return { source, mode: source.mode ?? 'demo' };
    } catch (error) {
      return { source: null, mode: configured.mode === 'live-local' ? 'live-local' as const : 'unavailable' as const, failure: safeFailure(error) };
    }
  });
  const [cache] = useState(() => new MemorySnapshotCache());
  const [state, setState] = useState<State>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    if (!runtime.source) { setState({ status: 'error', failure: runtime.failure! }); return; }
    loadSnapshot(runtime.source, cache).then(
      snapshot => { if (active) setState({ status: 'ready', snapshot }); },
      error => {
        if (!active) return;
        const failure = safeFailure(error);
        const snapshot = cache.read();
        setState(snapshot ? { status: 'ready', snapshot, notice: failure } : { status: 'error', failure });
      },
    );
    return () => { active = false; };
  }, [runtime, cache, attempt]);
  return <ModeContext.Provider value={runtime.mode}><Context.Provider value={{ state, retry: () => setAttempt(a => a + 1) }}>{children}</Context.Provider></ModeContext.Provider>;
}
export function useDataMode() { return useContext(ModeContext); }
export function useReportNotice() {
  const value = useContext(Context);
  return value?.state.status === 'ready' && value.state.notice ? { failure: value.state.notice, retry: value.retry } : null;
}
export function useReports() {
  const value = useContext(Context);
  if (!value) throw new Error('ApiProvider required');
  return value;
}
