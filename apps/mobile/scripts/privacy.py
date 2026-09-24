"""Read-only privacy gate: source, ephemeral development metadata, delivery archives.

No extraction, health endpoint reads, credential-file reads, or artifact writes.
Findings name the artifact and rule only; offending contents/paths are never printed.
"""
import argparse
import base64
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler
import zipfile

MOBILE = Path(__file__).resolve().parents[1]
LIMIT = 64 * 1024 * 1024
WINDOWS = re.compile(r'(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|\\\\[a-z0-9._-]+[\\/][a-z0-9._-]+)')
USER_PATH = re.compile(r'/(?:Users|home)/[^/\s]+/')
PRIVATE_PARTS = {'data', 'classifications', 'node_modules', '.expo', '.jest-cache',
                 '__pycache__', '.git', '.venv', 'coverage', 'dist', 'screenshots', 'logs'}
UPLOAD_ROOT_FILES = {'package.json', 'package-lock.json', 'app.json', 'tsconfig.json'}
# Narrow source-content rules, not entropy scanning or execution/data-flow analysis.
CONTEXT = r'(?:access_?token|refresh_?token|client_?secret|device_?token|bearer_?token|authorization|credential|secret)'
LITERAL = r'''(?P<quote>["'`])(?P<literal>[^"'`\r\n]{1,4096})(?P=quote)'''
CALLBACK = (r'(?:async\s+)?(?:\([^()\r\n]{0,120}\)\s*'
            r'(?::\s*[\w<> |\[\]]{1,80})?\s*=>\s*|function\s*\([^()\r\n]{0,120}\)\s*)'
            r'(?:\{\s*return\s+)?')
CREDENTIAL_LITERAL = re.compile(
    r'''(?<![\w$])["']?''' + CONTEXT + r'''["']?\s*'''
    + r'(?:\]?\s*[:=]\s*(?:[A-Za-z_][\w<>| \[\]]{0,79}\s*=\s*)?(?:'
    + CALLBACK + r')?|\([^()\r\n]{0,120}\)\s*\{\s*return\s+)' + LITERAL, re.IGNORECASE)
BEARER_LITERAL = re.compile(
    r'''(?<![\w$])["']?authorization["']?\s*\]?\s*[:,=]\s*''' + LITERAL, re.IGNORECASE)
PLACEHOLDERS = frozenset(('dummy', 'dummy-token', 'dummy-secret', 'test-only',
                         'test-only-token', 'test-only-secret', 'synthetic-placeholder',
                         'test-access', 'test-refresh'))
PRIVATE_REFERENCE = re.compile(
    r'''(?P<quote>["'`])(?P<reference>(?:[\w. @~+-]+[/\\])*'''
    r'''(?:whoop_(?:data|history)\.csv|whoop_tokens\.json|'''
    r'''[\w.-]*classifications?\.(?:csv|json|jsonl|ndjson|parquet|sqlite|db)|'''
    r'''(?:data|classifications)[/\\][\w./\\ @~+-]+\.(?:csv|json|jsonl|ndjson|parquet|sqlite|db)|'''
    r'''\.env(?:\.[\w.-]+)?))(?P=quote)''', re.IGNORECASE)
REFERENCE_CONTEXT = re.compile(
    r'(?:[:=\[,]\s*|\b(?:return|from|import)\s+|'
    r'\b(?:require|open|readFile|readFileSync|fetch|load|join|resolve|Path)\s*\(\s*)$')


def normalized(text):
    # Bounded passes cover JSON-in-JSON and ordinary JS/JSON escapes without eval.
    for _ in range(3):
        decoded = unquote(text)
        decoded = re.sub(r'\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))',
                         lambda m: chr(int(m[1] or m[2], 16)), decoded)
        decoded = re.sub(r'''\\([\\/"'`])''', r'\1', decoded)
        if decoded == text:
            break
        text = decoded
    return text


def content_rules(text):
    """Return rule IDs only. Never retain or print matched source/credential values."""
    text = normalized(text)
    rules = set()
    for pattern, bearer_only in ((CREDENTIAL_LITERAL, False), (BEARER_LITERAL, True)):
        for match in pattern.finditer(text):
            literal = match['literal']
            # Template interpolation and concatenated runtime values are not literals.
            if '${' in literal:
                continue
            bearer = re.fullmatch(r'Bearer\s+([A-Za-z0-9._~+/=-]+)', literal, re.IGNORECASE)
            value = bearer[1] if bearer else literal
            if value.lower() in PLACEHOLDERS:
                continue
            if bearer:
                rules.add('hardcoded_bearer_credential')
            elif not bearer_only and re.fullmatch(r'[A-Za-z0-9._~+/=-]{20,}', value):
                rules.add('hardcoded_credential_literal')
    for match in PRIVATE_REFERENCE.finditer(text):
        # Bare .env mentions in docs and basename filter rules are not file reads.
        # Backticks can also be Markdown; require source syntax for that form.
        context = REFERENCE_CONTEXT.search(text[max(0, match.start() - 100):match.start()])
        if match['quote'] == '`' and context and context[0].lstrip().startswith(','):
            context = None  # A Markdown list of filenames is not a source assignment.
        if (match['reference'].startswith('.env') or match['quote'] == '`') and not context:
            continue
        rules.add('private_backend_reference')
    return rules


def contains_path(text):
    return bool(WINDOWS.search(text) or WINDOWS.search(normalized(text)) or USER_PATH.search(normalized(text)))


def private_artifact(name):
    parts = PurePosixPath(name.rsplit('!', 1)[-1].replace('\\', '/')).parts
    base = parts[-1].lower() if parts else ''
    design_tokens = tuple(parts[-3:]) == ('src', 'design', 'tokens.ts')
    return (any(p.lower() in PRIVATE_PARTS for p in parts)
            or base.startswith('.env') or ('token' in base and not design_tokens) or 'classification' in base
            or base.endswith(('.csv', '.log', '.tsbuildinfo'))
            or base.startswith(('whoop_response', 'whoop_data', 'whoop_history', 'screenshot'))
            or base.endswith(('.bundle', '.map')))


def upload_candidate(name):
    parts = PurePosixPath(name).parts
    return (name in UPLOAD_ROOT_FILES or (len(parts) > 1 and parts[0] in ('app', 'src'))) and not private_artifact(name)


class Scanner:
    def __init__(self):
        self.findings = []
        self.checked = 0
        self.bytes = 0

    def finding(self, category, name, kind):
        safe = '<redacted artifact name>' if contains_path(name) else name
        item = {'category': category, 'artifact': safe, 'kind': kind}
        if item not in self.findings:
            self.findings.append(item)

    def scan(self, data, name, category, depth=0):
        self.checked += 1
        self.bytes += len(data)
        if self.bytes > LIMIT or self.checked > 4096 or depth > 3:
            self.finding(category, name, 'scan-limit-exceeded')
            return
        if contains_path(name):
            self.finding(category, name, 'absolute-path')
        if category == 'distribution' and private_artifact(name):
            self.finding(category, name, 'excluded-upload-artifact')
        # UTF-16 catches Windows/native metadata strings; byte decoding never executes JS.
        for text in (data.decode('utf-8', errors='replace'), data.decode('utf-16-le', errors='replace')):
            if contains_path(text):
                self.finding(category, name, 'absolute-path')
            for rule in sorted(content_rules(text)):
                self.finding(category, name, rule)
            for inline in re.findall(r'sourceMappingURL=data:application/json[^,\s]*;base64,([A-Za-z0-9+/=]+)', text):
                try:
                    self.scan(base64.b64decode(inline, validate=True), name + '#inline-map', category, depth + 1)
                except ValueError:
                    self.finding(category, name, 'unreadable-source-map')
        archive_name = name.lower()
        try:
            if zipfile.is_zipfile(io.BytesIO(data)):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir():
                            continue
                        if entry.file_size > LIMIT - self.bytes or self.checked >= 4096:
                            self.finding(category, name, 'scan-limit-exceeded')
                            break
                        self.archive_name(entry.filename, category, name)
                        self.scan(archive.read(entry), name + '!' + entry.filename, category, depth + 1)
            elif archive_name.endswith(('.tar', '.tar.gz', '.tgz')):
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                    for entry in archive:
                        if entry.isdir():
                            continue
                        if not entry.isfile():
                            self.finding(category, name, 'archive-link-or-special-file')
                            continue
                        if entry.size > LIMIT - self.bytes or self.checked >= 4096:
                            self.finding(category, name, 'scan-limit-exceeded')
                            break
                        self.archive_name(entry.name, category, name)
                        self.scan(archive.extractfile(entry).read(), name + '!' + entry.name, category, depth + 1)
            elif archive_name.endswith(('.zip', '.ipa', '.aab')):
                self.finding(category, name, 'unreadable-archive')
            elif archive_name.endswith(('.7z', '.rar', '.gz', '.bz2', '.xz', '.br')):
                self.finding(category, name, 'unsupported-compression')
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, tarfile.TarError):
            self.finding(category, name, 'unreadable-archive')

    def archive_name(self, member, category, name):
        if contains_path(member) or '..' in PurePosixPath(member.replace('\\', '/')).parts:
            self.finding(category, name, 'unsafe-archive-path')
        if category == 'distribution' and private_artifact(member):
            self.finding(category, name, 'excluded-upload-artifact')

    def file(self, path, category, name=None):
        name = name or path.name
        if path.is_symlink():
            self.finding(category, name, 'symlink-not-scanned')
            return
        try:
            with path.open('rb') as stream:
                data = stream.read(LIMIT + 1)
            self.scan(data, name, category)
        except OSError:
            self.finding(category, name, 'unreadable-artifact')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def development_url(scanner, url):
    """Only the local Metro server; never port 8000 or an API/sync path."""
    visited = set()
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(target, kind):
        parsed = urlsplit(target)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                or parsed.port != 8081 or parsed.username or parsed.password or parsed.fragment
                or not (parsed.path in ('', '/') or parsed.path.endswith(('.js', '.bundle', '.map')))):
            scanner.finding('development', 'Metro', 'refused-non-artifact-url')
            return
        if target in visited:
            return
        if len(visited) >= 16:
            scanner.finding('development', 'Metro', 'scan-limit-exceeded')
            return
        visited.add(target)
        try:
            with opener.open(target, timeout=20) as response:
                data = response.read(LIMIT + 1)
            scanner.scan(data, 'Metro-' + kind, 'development')
            text = data.decode('utf-8', errors='replace')
            if kind == 'page':
                for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', text):
                    read(urljoin(target, src.replace('&amp;', '&')), 'bundle')
            for mapping in re.findall(r'(?m)^[ \t]*//[#@]\s*sourceMappingURL=([^\s]+)[ \t]*$', text):
                if not mapping.startswith('data:'):
                    read(urljoin(target, mapping), 'map')
        except (OSError, ValueError):
            scanner.finding('development', 'Metro-' + kind, 'unreadable-artifact')
    read(url, 'page' if urlsplit(url).path in ('', '/') else 'bundle')


def source_files():
    # Explicit Git inventory excludes ignored local credentials/artifacts without reading them.
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '.'],
                            cwd=MOBILE, capture_output=True, check=True)
    return sorted(set(result.stdout.decode('utf-8').strip('\0').split('\0')) - {''})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='store_true')
    parser.add_argument('--upload-plan', action='store_true')
    parser.add_argument('--development-url')
    parser.add_argument('--metadata', type=Path, action='append', default=[])
    parser.add_argument('--artifact', type=Path, action='append', default=[])
    args = parser.parse_args()
    if not any((args.source, args.upload_plan, args.development_url, args.metadata, args.artifact)):
        parser.error('Choose a source, development or distribution check')
    scanner = Scanner()
    candidates = []
    if args.source or args.upload_plan:
        for name in source_files():
            if args.source:
                scanner.file(MOBILE / name, 'source', name)
            if args.upload_plan and upload_candidate(name):
                candidates.append(name)
                scanner.file(MOBILE / name, 'distribution', name)
    if args.development_url:
        development_url(scanner, args.development_url)
    for category, paths in [('development', args.metadata), ('distribution', args.artifact)]:
        for path in paths:
            if path.is_dir() and not path.is_symlink():
                for child in sorted(path.rglob('*')):
                    if child.is_file() or child.is_symlink():
                        scanner.file(child, category, child.relative_to(path).as_posix())
            else:
                scanner.file(path, category)
    print(json.dumps({'checked': scanner.checked, 'upload_candidates': candidates,
                      'findings': scanner.findings}, indent=2))
    return 1 if scanner.findings else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Privacy scan could not complete; artifact is not approved.')
        raise SystemExit(2) from None
