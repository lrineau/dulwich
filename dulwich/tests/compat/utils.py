# utils.py -- Git compatibility utilities
# Copyright (C) 2010 Google, Inc.
#
# Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
# General Public License as public by the Free Software Foundation; version 2.0
# or (at your option) any later version. You can redistribute it and/or
# modify it under the terms of either of these two licenses.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# You should have received a copy of the licenses; if not, see
# <http://www.gnu.org/licenses/> for a copy of the GNU General Public License
# and <http://www.apache.org/licenses/LICENSE-2.0> for a copy of the Apache
# License, Version 2.0.
#

"""Utilities for interacting with cgit."""

import errno
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time

from dulwich.repo import Repo
from dulwich.protocol import TCP_GIT_PORT

from dulwich.tests.utils import (
    rmtree_ro,
)
from dulwich.tests import (
    SkipTest,
    TestCase,
    )

_DEFAULT_GIT = 'git'
_VERSION_LEN = 4
_REPOS_DATA_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, 'data', 'repos'))


def git_version(git_path=_DEFAULT_GIT):
    """Attempt to determine the version of git currently installed.

    :param git_path: Path to the git executable; defaults to the version in
        the system path.
    :return: A tuple of ints of the form (major, minor, point, sub-point), or
        None if no git installation was found.
    """
    try:
        output = run_git_or_fail(['--version'], git_path=git_path)
    except OSError:
        return None
    version_prefix = b'git version '
    if not output.startswith(version_prefix):
        return None

    parts = output[len(version_prefix):].split(b'.')
    nums = []
    for part in parts:
        try:
            nums.append(int(part))
        except ValueError:
            break

    while len(nums) < _VERSION_LEN:
        nums.append(0)
    return tuple(nums[:_VERSION_LEN])


def require_git_version(required_version, git_path=_DEFAULT_GIT):
    """Require git version >= version, or skip the calling test.

    :param required_version: A tuple of ints of the form (major, minor, point,
        sub-point); ommitted components default to 0.
    :param git_path: Path to the git executable; defaults to the version in
        the system path.
    :raise ValueError: if the required version tuple has too many parts.
    :raise SkipTest: if no suitable git version was found at the given path.
    """
    found_version = git_version(git_path=git_path)
    if found_version is None:
        raise SkipTest('Test requires git >= %s, but c git not found' %
                       (required_version, ))

    if len(required_version) > _VERSION_LEN:
        raise ValueError('Invalid version tuple %s, expected %i parts' %
                         (required_version, _VERSION_LEN))

    required_version = list(required_version)
    while len(found_version) < len(required_version):
        required_version.append(0)
    required_version = tuple(required_version)

    if found_version < required_version:
        required_version = '.'.join(map(str, required_version))
        found_version = '.'.join(map(str, found_version))
        raise SkipTest('Test requires git >= %s, found %s' %
                       (required_version, found_version))


def run_git(args, git_path=_DEFAULT_GIT, input=None, capture_stdout=False,
            **popen_kwargs):
    """Run a git command.

    Input is piped from the input parameter and output is sent to the standard
    streams, unless capture_stdout is set.

    :param args: A list of args to the git command.
    :param git_path: Path to to the git executable.
    :param input: Input data to be sent to stdin.
    :param capture_stdout: Whether to capture and return stdout.
    :param popen_kwargs: Additional kwargs for subprocess.Popen;
        stdin/stdout args are ignored.
    :return: A tuple of (returncode, stdout contents). If capture_stdout is
        False, None will be returned as stdout contents.
    :raise OSError: if the git executable was not found.
    """

    env = popen_kwargs.pop('env', {})
    env['LC_ALL'] = env['LANG'] = 'C'

    args = [git_path] + args
    popen_kwargs['stdin'] = subprocess.PIPE
    if capture_stdout:
        popen_kwargs['stdout'] = subprocess.PIPE
    else:
        popen_kwargs.pop('stdout', None)
    p = subprocess.Popen(args, env=env, **popen_kwargs)
    stdout, stderr = p.communicate(input=input)
    return (p.returncode, stdout)


def run_git_or_fail(args, git_path=_DEFAULT_GIT, input=None, **popen_kwargs):
    """Run a git command, capture stdout/stderr, and fail if git fails."""
    if 'stderr' not in popen_kwargs:
        popen_kwargs['stderr'] = subprocess.STDOUT
    returncode, stdout = run_git(args, git_path=git_path, input=input,
                                 capture_stdout=True, **popen_kwargs)
    if returncode != 0:
        raise AssertionError("git with args %r failed with %d: %r" % (
            args, returncode, stdout))
    return stdout


def import_repo_to_dir(name):
    """Import a repo from a fast-export file in a temporary directory.

    These are used rather than binary repos for compat tests because they are
    more compact and human-editable, and we already depend on git.

    :param name: The name of the repository export file, relative to
        dulwich/tests/data/repos.
    :returns: The path to the imported repository.
    """
    temp_dir = tempfile.mkdtemp()
    export_path = os.path.join(_REPOS_DATA_DIR, name)
    temp_repo_dir = os.path.join(temp_dir, name)
    export_file = open(export_path, 'rb')
    run_git_or_fail(['init', '--quiet', '--bare', temp_repo_dir])
    run_git_or_fail(['fast-import'], input=export_file.read(),
                    cwd=temp_repo_dir)
    export_file.close()
    return temp_repo_dir


def check_for_daemon(limit=10, delay=0.1, timeout=0.1, port=TCP_GIT_PORT):
    """Check for a running TCP daemon.

    Defaults to checking 10 times with a delay of 0.1 sec between tries.

    :param limit: Number of attempts before deciding no daemon is running.
    :param delay: Delay between connection attempts.
    :param timeout: Socket timeout for connection attempts.
    :param port: Port on which we expect the daemon to appear.
    :returns: A boolean, true if a daemon is running on the specified port,
        false if not.
    """
    for _ in range(limit):
        time.sleep(delay)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(delay)
        try:
            s.connect(('localhost', port))
            return True
        except socket.timeout:
            pass
        except socket.error as e:
            if getattr(e, 'errno', False) and e.errno != errno.ECONNREFUSED:
                raise
            elif e.args[0] != errno.ECONNREFUSED:
                raise
        finally:
            s.close()
    return False


class CompatTestCase(TestCase):
    """Test case that requires git for compatibility checks.

    Subclasses can change the git version required by overriding
    min_git_version.
    """

    min_git_version = (1, 5, 0)

    def setUp(self):
        super(CompatTestCase, self).setUp()
        require_git_version(self.min_git_version)

    def assertObjectStoreEqual(self, store1, store2):
        self.assertEqual(sorted(set(store1)), sorted(set(store2)))

    def assertReposEqual(self, repo1, repo2):
        self.assertEqual(repo1.get_refs(), repo2.get_refs())
        self.assertObjectStoreEqual(repo1.object_store, repo2.object_store)

    def assertReposNotEqual(self, repo1, repo2):
        refs1 = repo1.get_refs()
        objs1 = set(repo1.object_store)
        refs2 = repo2.get_refs()
        objs2 = set(repo2.object_store)
        self.assertFalse(refs1 == refs2 and objs1 == objs2)

    def import_repo(self, name):
        """Import a repo from a fast-export file in a temporary directory.

        :param name: The name of the repository export file, relative to
            dulwich/tests/data/repos.
        :returns: An initialized Repo object that lives in a temporary directory.
        """
        path = import_repo_to_dir(name)
        repo = Repo(path)
        def cleanup():
            repo.close()
            rmtree_ro(os.path.dirname(path.rstrip(os.sep)))
        self.addCleanup(cleanup)
        return repo

    def create_new_worktree(self, repo_dir, branch):
        """Create a new worktree using git-worktree.

        :param repo_dir: The directory of the main working tree.
        :param branch: The branch or commit to checkout in the new worktree.

        :returns: The path to the new working tree.
        """
        temp_dir = tempfile.mkdtemp()
        run_git_or_fail(['worktree', 'add', temp_dir, branch],
                        cwd=repo_dir)
        return temp_dir

    def debug_repo(self, s, repo):
        print('{}: {}'.format(s, repo))
        print('controldir: {}'.format(repo.controldir()))
        print(' commondir: {}'.format(repo.commondir()))
        for r in repo.get_refs().keys():
            print('{}: {}'.format(r, repo.refs[r]))

