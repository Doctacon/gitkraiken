"""Exercise existing commit fixtures without the host's inferred Git identity."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

real_git = shutil.which('git')
assert real_git
with tempfile.TemporaryDirectory(prefix='rhizome-strict-git-') as temporary:
    shim = Path(temporary, 'git')
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(real_git) + ' -c user.useConfigOnly=true "$@"\n')
    shim.chmod(0o700)
    env = dict(os.environ, PATH=temporary + os.pathsep + os.environ['PATH'])
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_commit_representations.py',
                             '-q', '--tb=short', '-p', 'no:cacheprovider'], env=env, timeout=60)
    sys.exit(result.returncode)
