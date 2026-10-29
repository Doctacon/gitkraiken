"""Bounded real bootstrap lifecycle in an owned disposable home; retain on failure."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPORT = Path(__file__).resolve().parent
A = '44cdd1641df92e4084690cbd7288ad9d34e38d92'
B = '19086a362dd9ac48bdffdd461816add47ca7c6ed'
START = time.monotonic()
DEADLINE = START + 420
sandbox = Path(tempfile.mkdtemp(prefix='rhizome-m1-live-', dir='/tmp')).resolve()
home = sandbox / 'home with spaces'
home.mkdir()
root = home / 'private apps' / 'rhizome'
state = home / 'application-state'
state.mkdir()
temporary = sandbox / 'tmp'
temporary.mkdir()
bootstrap = sandbox / 'install.sh'
env = {
    'PATH': os.environ['PATH'], 'HOME': str(home), 'TMPDIR': str(temporary),
    'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_DATA_HOME': str(home / 'data'),
    'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_STATE_HOME': str(home / 'state'),
    'RHIZOME_INSTALL_ROOT': str(root), 'RHIZOME_STATE_HOME': str(state),
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/bin/false', 'SSH_ASKPASS': '/bin/false',
    'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
}
network_keys = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy',
                'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE')
for key in network_keys:
    if key in os.environ:
        env[key] = os.environ[key]
protected = {
    state / 'draft.json': '{"message":"synthetic release-validation draft"}\n',
    home / '.profile': '# synthetic profile: preserve\n',
    home / '.tmux.conf': '# synthetic tmux config: preserve\n',
    home / '.gitconfig': '[user]\n\tname = Synthetic User\n\temail = synthetic@example.invalid\n',
    home / 'unrelated-tool': 'synthetic unrelated tool: preserve\n',
}
for path, content in protected.items():
    path.write_text(content)
report = {'candidate': B, 'previous_revision': A, 'sandbox': str(sandbox),
          'steps': [], 'passed': False, 'sandbox_removed': False}


def redact(text):
    for key in network_keys:
        value = env.get(key, '')
        if value and '@' in value and '://' in value:
            text = text.replace(value, '<REDACTED_PROXY>')
    return re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1<REDACTED>@', text)


def save():
    report['elapsed_seconds'] = round(time.monotonic() - START, 2)
    (REPORT / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')


def run(name, command, bound=180):
    remaining = DEADLINE - time.monotonic()
    if remaining <= 0:
        raise RuntimeError('Lifecycle time bound reached; sandbox retained')
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=sandbox, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    timed_out = False
    try:
        output, _ = process.communicate(timeout=min(bound, remaining))
    except subprocess.TimeoutExpired:
        timed_out = True
        # Signal only this command's owned process group. Never delete its sandbox
        # on an uncertain outcome, even after the direct process has stopped.
        os.killpg(process.pid, signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=5)
    output = redact(output)
    (REPORT / f'{name}.txt').write_text(output)
    report['steps'].append({'name': name, 'exit_code': process.returncode,
                            'timed_out': timed_out,
                            'elapsed_seconds': round(time.monotonic() - started, 2)})
    save()
    print(f'{name}: exit {process.returncode}, timeout={timed_out}', flush=True)
    if timed_out or process.returncode:
        raise RuntimeError(f'{name} did not pass; see its redacted log')
    return output


def preserved():
    for path, content in protected.items():
        assert path.read_text() == content, f'Protected synthetic file changed: {path.name}'


def selected(revision):
    assert (root / 'current').read_text().strip() == f'revisions/{revision}'
    preserved()


try:
    expected = subprocess.check_output(['git', 'show', f'{B}:scripts/install.sh'], timeout=10)
    run('download-bootstrap', ['curl', '-q', '--fail', '--location', '--proto', '=https',
                               '--proto-redir', '=https', '--tlsv1.2', '--output', str(bootstrap),
                               f'https://raw.githubusercontent.com/Doctacon/rhizome/{B}/scripts/install.sh'], 30)
    assert bootstrap.read_bytes() == expected, 'Downloaded bootstrap differs from reviewed commit'
    report['bootstrap_sha256'] = hashlib.sha256(expected).hexdigest()
    run('install', ['sh', str(bootstrap), '--revision', A])
    selected(A)
    run('installed-help', [str(root / 'bin' / 'rhizome'), '--help'], 20)
    payload = root / 'store' / A
    python = list((payload / 'python').glob('*/bin/python3.13'))
    assert len(python) == 1, 'Expected one private managed Python'
    assert python[0].resolve().is_relative_to(payload)
    assert run('managed-python', [str(python[0]), '--version'], 20).strip() == 'Python 3.13.13'
    assert run('private-uv', [str(payload / 'uv' / 'bin' / 'uv'), '--version'], 20).startswith('uv 0.11.7')
    run('update', ['sh', str(bootstrap), '--revision', B])
    selected(B)
    assert (root / 'previous').read_text().strip() == f'revisions/{A}'
    run('updated-help', [str(root / 'bin' / 'rhizome'), '--help'], 20)
    run('rollback', ['sh', str(bootstrap), '--rollback'], 20)
    selected(A)
    assert (root / 'previous').read_text().strip() == f'revisions/{B}'
    run('rolled-back-help', [str(root / 'bin' / 'rhizome'), '--help'], 20)
    run('uninstall', ['sh', str(bootstrap), '--uninstall'], 30)
    assert not root.exists()
    preserved()
    report['passed'] = True
    shutil.rmtree(sandbox)
    report['sandbox_removed'] = True
except Exception as error:
    report['error'] = redact(f'{type(error).__name__}: {error}')
finally:
    save()
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if report['passed'] else 1)
