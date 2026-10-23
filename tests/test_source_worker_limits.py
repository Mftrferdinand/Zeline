"""Platform memory baseline must be measured before lowering address-space limits."""
try:
    import resource
except ImportError:
    resource = None
import unittest
from pathlib import Path
from unittest.mock import patch
from zeline import source_worker_limits as limits


@unittest.skipIf(resource is None, 'POSIX resource controls')
class DarwinBaselineTests(unittest.TestCase):
    def test_darwin_uses_measured_virtual_baseline_without_procfs(self):
        budget = 128 * 1024 * 1024
        baseline = 400 * 1024 * 1024 * 1024
        with patch('sys.platform', 'darwin'), patch.object(Path, 'exists', return_value=False), \
                patch.object(limits, '_darwin_virtual_size', create=True, return_value=baseline) as measured, \
                patch.object(resource, 'getrlimit', return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY)), \
                patch.object(resource, 'setrlimit') as set_limit:
            limits.set_memory_limit(budget)
        measured.assert_called_once_with()
        set_limit.assert_any_call(resource.RLIMIT_DATA, (baseline + budget, baseline + budget))
        set_limit.assert_any_call(resource.RLIMIT_AS, (baseline + budget, baseline + budget))
        set_limit.assert_any_call(resource.RLIMIT_CORE, (0, 0))

    def test_linux_retains_absolute_data_budget(self):
        budget = 128 * 1024 * 1024
        with patch('sys.platform', 'linux'), patch.object(Path, 'exists', return_value=False), \
                patch.object(limits, '_darwin_virtual_size') as measured, \
                patch.object(resource, 'getrlimit', return_value=(resource.RLIM_INFINITY, resource.RLIM_INFINITY)), \
                patch.object(resource, 'setrlimit') as set_limit:
            limits.set_memory_limit(budget)
        measured.assert_not_called()
        set_limit.assert_any_call(resource.RLIMIT_DATA, (budget, budget))

    def test_failed_darwin_measurement_never_installs_zero_baseline_limit(self):
        with patch('sys.platform', 'darwin'), \
                patch.object(limits, '_darwin_virtual_size', side_effect=OSError('denied')), \
                patch.object(resource, 'setrlimit') as set_limit:
            with self.assertRaises(OSError):
                limits.set_memory_limit(128 * 1024 * 1024)
        set_limit.assert_not_called()


class LibprocAbiTests(unittest.TestCase):
    def probe(self, *, short=False, empty=False):
        import ctypes
        from types import SimpleNamespace
        def pidinfo(pid, flavor, arg, pointer, size):
            self.assertEqual(flavor, 4)
            self.assertEqual(size, 96)
            pointer._obj.pti_virtual_size = 0 if empty else 400 * 1024 * 1024 * 1024
            return size - 1 if short else size
        with patch.object(ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=pidinfo)):
            return limits._darwin_virtual_size()

    def test_complete_taskinfo_reports_virtual_reservation_not_rss(self):
        self.assertEqual(self.probe(), 400 * 1024 * 1024 * 1024)

    def test_short_taskinfo_rejected(self):
        with self.assertRaises(OSError):
            self.probe(short=True)

    def test_zero_taskinfo_rejected(self):
        with self.assertRaises(OSError):
            self.probe(empty=True)


def test_worker_environment_is_scoped_and_darwin_space_efficient():
    import os
    from unittest.mock import patch
    from zeline import source_worker_limits as limits
    original = {'PATH': '/trusted/bin', 'ZELINE_API_KEY': 'not-a-real-key',
                'PYTHONPATH': '/untrusted/python', 'MallocSpaceEfficient': '0'}
    with patch.dict(os.environ, original, clear=True):
        # Windows canonicalizes environment keys; compare the actual native
        # mapping before/after the helper, not the caller's mixed-case fixture.
        before = dict(os.environ)
        with patch('sys.platform', 'darwin'):
            environment = limits.worker_environment()
            assert environment == {'PATH': '/trusted/bin', 'MallocSpaceEfficient': '1'}
        with patch('sys.platform', 'linux'):
            assert limits.worker_environment() == {'PATH': '/trusted/bin'}
        assert dict(os.environ) == before
