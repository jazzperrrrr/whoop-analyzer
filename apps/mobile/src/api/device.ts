import { ApiFailure } from './errors';

// Supplied at runtime by future pairing/Keychain integration, never public config.
export type DeviceCredential = () => Promise<string | null>;
export type DeviceTransport = {
  profile: 'native-device';
  approvedOrigin: string;
  credential: DeviceCredential;
};
export function deviceBase(base: string, approved: string): string {
  const hostname = '(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}';
  if (!new RegExp(`^https://${hostname}$`).test(approved) || base !== approved) {
    throw new ApiFailure('invalid_configuration', false);
  }
  try {
    if (new URL(approved).origin !== approved) throw new Error();
    return approved;
  } catch { throw new ApiFailure('invalid_configuration', false); }
}
