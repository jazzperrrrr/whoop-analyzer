import { createContext, useContext, useEffect, useState, type PropsWithChildren } from 'react';
import type { ApiClient } from './client';
import { VisualDemoApiClient } from './demo';
import { loadSnapshot, MemorySnapshotCache, type CachedSnapshot } from '../storage/snapshot';

type State = { status: 'loading' } | { status: 'ready'; snapshot: CachedSnapshot } | { status: 'error' };
const Context = createContext<{ state: State; retry: () => void } | null>(null);
export function ApiProvider({ children, client }: PropsWithChildren<{ client?: ApiClient }>) {
  const [source] = useState(() => client ?? new VisualDemoApiClient());
  const [cache] = useState(() => new MemorySnapshotCache());
  const [state, setState] = useState<State>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    loadSnapshot(source, cache).then(
      snapshot => { if (active) setState({ status: 'ready', snapshot }); },
      () => { if (active) setState({ status: 'error' }); },
    );
    return () => { active = false; };
  }, [source, cache, attempt]);
  return <Context.Provider value={{ state, retry: () => setAttempt(a => a + 1) }}>{children}</Context.Provider>;
}
export function useReports() {
  const value = useContext(Context);
  if (!value) throw new Error('ApiProvider required');
  return value;
}
