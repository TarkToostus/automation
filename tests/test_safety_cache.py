"""Tests for _safety_cache — SHA-256 verdict cache for tark_cli safety screen.

Each test sets XDG_CACHE_HOME to a tempdir so we don't touch the real cache.
"""
import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure the automation/ dir is on sys.path so `import _safety_cache` works.
_AUTOMATION_DIR = str(Path(__file__).resolve().parent.parent)
if _AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATION_DIR)


class SafetyCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        # Drop any stale env that would affect TTL or disable.
        os.environ.pop('TARK_SAFETY_CACHE', None)
        os.environ.pop('TARK_SAFETY_CACHE_TTL_SEC', None)
        os.environ.pop('SAFETY_CHECK_MODEL', None)
        # Fresh import so module-level path/cache reads happen in this tempdir.
        if '_safety_cache' in sys.modules:
            del sys.modules['_safety_cache']
        self.sc = importlib.import_module('_safety_cache')

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop('XDG_CACHE_HOME', None)

    def test_1_fresh_cache_miss(self):
        self.assertFalse(self.sc.lookup('wiki', 'title', 'body'))

    def test_2_record_then_hit(self):
        self.sc.record_safe('wiki', 'title', 'body')
        self.assertTrue(self.sc.lookup('wiki', 'title', 'body'))

    def test_3_ttl_expiry(self):
        self.sc.record_safe('wiki', 'title', 'body')
        self.assertTrue(self.sc.lookup('wiki', 'title', 'body'))
        # Force expired via env var, then verify miss.
        os.environ['TARK_SAFETY_CACHE_TTL_SEC'] = '0'
        time.sleep(0.01)  # ensure ts > 0 has elapsed
        self.assertFalse(self.sc.lookup('wiki', 'title', 'body'))

    def test_4_mode_keyed(self):
        # Recording under 'wiki' mode should not satisfy 'task' mode lookup.
        self.sc.record_safe('wiki', 'title', 'body')
        self.assertFalse(self.sc.lookup('task', 'title', 'body'))

    def test_5_model_keyed(self):
        # Verdict for one model should not satisfy lookup under a different model.
        os.environ['SAFETY_CHECK_MODEL'] = 'gemini-2.5-pro'
        self.sc.record_safe('wiki', 'title', 'body')
        self.assertTrue(self.sc.lookup('wiki', 'title', 'body'))
        os.environ['SAFETY_CHECK_MODEL'] = 'gemini-3.1-pro'
        self.assertFalse(self.sc.lookup('wiki', 'title', 'body'))

    def test_6_disabled_via_env(self):
        self.sc.record_safe('wiki', 'title', 'body')
        os.environ['TARK_SAFETY_CACHE'] = '0'
        self.assertFalse(self.sc.lookup('wiki', 'title', 'body'))

    def test_7_lru_eviction_when_capped(self):
        # Lower the cap, then write more than CAP entries; assert eviction.
        original_cap = self.sc._DEFAULT_CAP
        self.sc._DEFAULT_CAP = 10
        try:
            for i in range(15):
                self.sc.record_safe('wiki', f't{i}', f'b{i}')
            import json as _json
            data = _json.loads((Path(self._tmp.name) / 'tark_cli' / 'safety_verdicts.json').read_text())
            self.assertLessEqual(len(data), self.sc._DEFAULT_CAP)
            # The 5 oldest should have been evicted; the 5 newest should remain.
            self.assertTrue(self.sc.lookup('wiki', 't14', 'b14'))
        finally:
            self.sc._DEFAULT_CAP = original_cap

    def test_8_corrupt_cache_file_recovers(self):
        cache_dir = Path(self._tmp.name) / 'tark_cli'
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / 'safety_verdicts.json').write_text('not-json-at-all{[')
        # lookup should return False without crashing.
        self.assertFalse(self.sc.lookup('wiki', 'title', 'body'))
        # record_safe should reset and write valid JSON.
        self.sc.record_safe('wiki', 'title', 'body')
        self.assertTrue(self.sc.lookup('wiki', 'title', 'body'))


if __name__ == '__main__':
    unittest.main()
