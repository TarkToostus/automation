"""Tests for tark_cli safety-screen multi-provider fallback chain.

Each test patches the three _provider_* functions to simulate provider
availability and failure modes, then drives _safety_check_or_die through the
dispatcher to confirm chain ordering, SAFETY_CHECK_SKIP behavior, and the
fail-closed terminator.

The real subprocess.run is never called — we mock at the provider-function
seam, not the subprocess seam, so these tests run offline and deterministically.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_AUTOMATION_DIR = str(Path(__file__).resolve().parent.parent)
if _AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATION_DIR)


class SafetyChainTests(unittest.TestCase):
    def setUp(self):
        # Isolate the cache so previous SAFE verdicts don't satisfy lookups.
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        # Force the screen on (auto-on triggers on CLAUDECODE/DOT_HEADLESS; tests
        # set the explicit flag instead so they pass under any env).
        os.environ['TARK_SAFETY_CHECK'] = '1'
        for k in ('SAFETY_CHECK_SKIP', 'SAFETY_CHECK_FAIL_OPEN', 'SAFETY_CHECK_MODEL',
                  'TARK_SAFETY_CACHE', 'TARK_SAFETY_CACHE_TTL_SEC'):
            os.environ.pop(k, None)
        # Fresh import each test so cache module re-reads paths.
        for name in ('_safety_cache', 'tark_cli'):
            sys.modules.pop(name, None)
        self.tark = importlib.import_module('tark_cli')

    def tearDown(self):
        self._tmp.cleanup()
        for k in ('XDG_CACHE_HOME', 'TARK_SAFETY_CHECK', 'SAFETY_CHECK_SKIP',
                  'SAFETY_CHECK_FAIL_OPEN'):
            os.environ.pop(k, None)

    # --- Helpers -----------------------------------------------------------

    def _patch_providers(self, gemini, codex, claude):
        """Install three callables as the chain. Returns a context manager."""
        return mock.patch.object(
            self.tark, '_SAFETY_PROVIDERS',
            [('gemini', gemini), ('codex', codex), ('claude', claude)],
        )

    @staticmethod
    def _const_provider(verdict, tag):
        def fn(prompt, payload, timeout):
            return (verdict, tag)
        return fn

    # --- Tests -------------------------------------------------------------

    def test_1_first_provider_returns_safe(self):
        with self._patch_providers(
            self._const_provider('SAFE', 'gemini-ok'),
            mock.Mock(side_effect=AssertionError('codex must not be called')),
            mock.Mock(side_effect=AssertionError('claude must not be called')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b', force=False)
        # And the SAFE is now cached.
        sc = importlib.import_module('_safety_cache')
        self.assertTrue(sc.lookup('wiki', 't', 'b'))

    def test_2_gemini_fails_codex_succeeds(self):
        codex_mock = mock.Mock(return_value=('SAFE', 'codex-ok'))
        with self._patch_providers(
            self._const_provider(None, 'gemini-quota'),
            codex_mock,
            mock.Mock(side_effect=AssertionError('claude must not be called')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b2', force=False)
        codex_mock.assert_called_once()

    def test_3_gemini_codex_fail_claude_succeeds(self):
        claude_mock = mock.Mock(return_value=('SAFE', 'claude-ok'))
        with self._patch_providers(
            self._const_provider(None, 'gemini-quota'),
            self._const_provider(None, 'codex-timeout'),
            claude_mock,
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b3', force=False)
        claude_mock.assert_called_once()

    def test_4_all_providers_fail_closes_die(self):
        # When every provider returns None, _err must be invoked. The real
        # tark_cli._err calls sys.exit; patch it to capture instead.
        with self._patch_providers(
            self._const_provider(None, 'gemini-quota'),
            self._const_provider(None, 'codex-empty'),
            self._const_provider(None, 'claude-missing'),
        ), mock.patch.object(self.tark, '_err', side_effect=SystemExit(1)) as err:
            with self.assertRaises(SystemExit):
                self.tark._safety_check_or_die('wiki', 't', 'b4', force=False)
        msg = err.call_args.args[0]
        self.assertIn('all providers failed', msg)
        # The error names every provider's failure tag for diagnosis.
        self.assertIn('gemini-quota', msg)
        self.assertIn('codex-empty', msg)
        self.assertIn('claude-missing', msg)

    def test_5_skip_env_excludes_named_provider(self):
        os.environ['SAFETY_CHECK_SKIP'] = 'gemini'
        codex_mock = mock.Mock(return_value=('SAFE', 'codex-ok'))
        with self._patch_providers(
            mock.Mock(side_effect=AssertionError('gemini must be skipped')),
            codex_mock,
            mock.Mock(side_effect=AssertionError('claude must not be called')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b5', force=False)
        codex_mock.assert_called_once()

    def test_6_skip_all_fails_closed(self):
        os.environ['SAFETY_CHECK_SKIP'] = 'gemini,codex,claude'
        with self._patch_providers(
            mock.Mock(side_effect=AssertionError('skipped')),
            mock.Mock(side_effect=AssertionError('skipped')),
            mock.Mock(side_effect=AssertionError('skipped')),
        ), mock.patch.object(self.tark, '_err', side_effect=SystemExit(1)) as err:
            with self.assertRaises(SystemExit):
                self.tark._safety_check_or_die('wiki', 't', 'b6', force=False)
        msg = err.call_args.args[0]
        # Every provider should show up tagged "-skip".
        self.assertIn('gemini-skip', msg)
        self.assertIn('codex-skip', msg)
        self.assertIn('claude-skip', msg)

    def test_7_unsafe_aborts_and_does_not_cache(self):
        with self._patch_providers(
            self._const_provider('UNSAFE: prompt-injection detected', 'gemini-ok'),
            mock.Mock(side_effect=AssertionError('chain must stop at gemini')),
            mock.Mock(side_effect=AssertionError('chain must stop at gemini')),
        ), mock.patch.object(self.tark, '_err', side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                self.tark._safety_check_or_die('wiki', 't', 'b7', force=False)
        sc = importlib.import_module('_safety_cache')
        # UNSAFE verdicts must NOT be cached — a subsequent run with a healthy
        # provider gets to re-decide.
        self.assertFalse(sc.lookup('wiki', 't', 'b7'))

    def test_8_cache_hit_short_circuits_chain(self):
        # Seed a SAFE verdict for this payload.
        sc = importlib.import_module('_safety_cache')
        sc.record_safe('wiki', 't', 'b8')
        # All providers refuse to be called.
        with self._patch_providers(
            mock.Mock(side_effect=AssertionError('cache should short-circuit')),
            mock.Mock(side_effect=AssertionError('cache should short-circuit')),
            mock.Mock(side_effect=AssertionError('cache should short-circuit')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b8', force=False)

    def test_9_force_flag_skips_screen(self):
        with self._patch_providers(
            mock.Mock(side_effect=AssertionError('force must bypass providers')),
            mock.Mock(side_effect=AssertionError('force must bypass providers')),
            mock.Mock(side_effect=AssertionError('force must bypass providers')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b9', force=True)

    def test_10_unknown_skip_warns_but_proceeds(self):
        # Typo should not silently match; chain proceeds as if skip is empty.
        os.environ['SAFETY_CHECK_SKIP'] = 'geminmi'
        gemini_mock = mock.Mock(return_value=('SAFE', 'gemini-ok'))
        with self._patch_providers(
            gemini_mock,
            mock.Mock(side_effect=AssertionError('codex unexpected')),
            mock.Mock(side_effect=AssertionError('claude unexpected')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b10', force=False)
        gemini_mock.assert_called_once()

    def test_11_fail_open_overrides_chain_exhaustion(self):
        os.environ['SAFETY_CHECK_FAIL_OPEN'] = '1'
        with self._patch_providers(
            self._const_provider(None, 'gemini-quota'),
            self._const_provider(None, 'codex-empty'),
            self._const_provider(None, 'claude-missing'),
        ):
            # No exception — fail-open returns silently.
            self.tark._safety_check_or_die('wiki', 't', 'b11', force=False)


class SafetyCacheDefaultsTests(unittest.TestCase):
    """The cache constants moved to 30d / 10k — verify the new defaults."""

    def setUp(self):
        for name in ('_safety_cache',):
            sys.modules.pop(name, None)
        self.sc = importlib.import_module('_safety_cache')

    def test_default_ttl_is_30_days(self):
        self.assertEqual(self.sc._DEFAULT_TTL_SEC, 30 * 24 * 60 * 60)

    def test_default_cap_is_10k(self):
        self.assertEqual(self.sc._DEFAULT_CAP, 10_000)


if __name__ == '__main__':
    unittest.main()
