import type { TodayResponse, SleepResponse } from '../types/api-v1';
import type { ApiClient } from '../api/client';
import { ApiFailure } from '../api/errors';

export type SnapshotIdentity = Pick<TodayResponse, 'schema_version' | 'analysis_version' | 'snapshot_id'>;
export function sameSnapshot(a: SnapshotIdentity, b: SnapshotIdentity): boolean {
  return a.schema_version === b.schema_version && a.analysis_version === b.analysis_version && a.snapshot_id === b.snapshot_id;
}
export type CachedSnapshot = { today: TodayResponse; sleep: SleepResponse; cached_at: string };
export interface SnapshotCache {
  read(): CachedSnapshot | null;
  write(value: CachedSnapshot): void;
  clear(): void;
}
export class MemorySnapshotCache implements SnapshotCache {
  private value: CachedSnapshot | null = null;
  read() { return this.value; }
  write(value: CachedSnapshot) {
    if (!sameSnapshot(value.today, value.sleep)) throw new Error('Incoherent snapshot');
    this.value = value;
  }
  clear() { this.value = null; }
}
export async function loadSnapshot(client: ApiClient, cache: SnapshotCache): Promise<CachedSnapshot> {
  await client.getHealth?.();
  for (let attempt = 0; attempt < 2; attempt++) {
    // Windows snapshot readers can contend on the existing nonblocking OS lock.
    // Acquire sequentially; identity still detects any publication between these reads.
    const today = await client.getToday();
    const sleep = await client.getLatestSleep();
    if (sameSnapshot(today, sleep)) {
      const value = { today, sleep, cached_at: new Date().toISOString() };
      cache.write(value); // Atomic pair: never mix separately acquired input identities.
      return value;
    }
  }
  // No writes on failure: the provider may display the prior pair with an explicit notice.
  throw new ApiFailure('incoherent_snapshot');
}
