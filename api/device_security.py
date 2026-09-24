"""Opt-in device transport policy. No token creation, credentials or networking."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re

DEVICE_PATHS = frozenset(('/api/v1/health', '/api/v1/today', '/api/v1/sleep/latest'))
REPOSITORY = Path(__file__).resolve().parents[1]
HOST = re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}')
HEX_256 = re.compile(r'[0-9a-f]{64}')


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DeviceProfile:
    host: str
    verifier_file: Path = field(repr=False)
    clock: object = field(default=utc_now, repr=False, compare=False)

    def __post_init__(self):
        # A future HTTPS proxy preserves this exact DNS Host (default port 443).
        # Paths/ports/wildcards/IP aliases and a local-profile Host cannot opt in.
        if not HOST.fullmatch(self.host):
            raise ValueError('Invalid device profile configuration')
        path = Path(self.verifier_file)
        if not path.is_absolute() or path.resolve().is_relative_to(REPOSITORY):
            raise ValueError('Invalid device profile configuration')
        object.__setattr__(self, 'verifier_file', path)

    def verify(self, authorization):
        """Reload on each request: deleting/replacing the verifier revokes access."""
        try:
            if len(authorization) != 1:
                return False
            match = re.fullmatch(r'Bearer ([0-9a-f]{64})', authorization[0], re.IGNORECASE)
            if not match:
                return False
            path = self.verifier_file.resolve(strict=True)
            if path.is_relative_to(REPOSITORY):
                return False
            with path.open('rb') as file:
                raw = file.read(4097)
            if len(raw) > 4096:
                return False
            record = json.loads(raw)
            if (not isinstance(record, dict) or set(record) != {'version', 'sha256', 'expires_at'}
                    or type(record['version']) is not int or record['version'] != 1
                    or not isinstance(record['sha256'], str) or not HEX_256.fullmatch(record['sha256'])
                    or not isinstance(record['expires_at'], str)
                    or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', record['expires_at'])):
                return False
            expires = datetime.fromisoformat(record['expires_at'].replace('Z', '+00:00'))
            candidate = hashlib.sha256(match[1].encode('ascii')).hexdigest()
            valid = hmac.compare_digest(candidate, record['sha256'])
            return valid and self.clock() < expires
        except (OSError, ValueError, TypeError, OverflowError):
            # Never surface file paths, verifier contents or token rejection reasons.
            return False

    def permits(self, request):
        return (request.client is not None and request.client.host in ('127.0.0.1', '::1')
                and request.headers.getlist('host') == [self.host]
                and request.method == 'GET' and request.url.path in DEVICE_PATHS
                and not request.scope.get('query_string')
                and not request.headers.getlist('origin')
                and self.verify(request.headers.getlist('authorization')))


def configured_device_profile(environ):
    """Explicit profile selection; local mode never reads the verifier file."""
    profile = environ.get('WHOOP_API_PROFILE', 'local')
    if profile == 'local':
        return None
    if profile != 'native-device':
        raise ValueError('Invalid device profile configuration')
    try:
        return DeviceProfile(environ['WHOOP_DEVICE_HOST'], Path(environ['WHOOP_DEVICE_VERIFIER_FILE']))
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('Invalid device profile configuration') from None
