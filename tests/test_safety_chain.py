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

    def test_5b_skip_is_case_insensitive(self):
        # User intent vs behavior: SAFETY_CHECK_SKIP=GEMINI should skip gemini,
        # not warn "unknown provider" and run gemini anyway.
        os.environ['SAFETY_CHECK_SKIP'] = 'GEMINI'
        codex_mock = mock.Mock(return_value=('SAFE', 'codex-ok'))
        with self._patch_providers(
            mock.Mock(side_effect=AssertionError('gemini must be skipped (case)')),
            codex_mock,
            mock.Mock(side_effect=AssertionError('claude must not be called')),
        ):
            self.tark._safety_check_or_die('wiki', 't', 'b5b', force=False)
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


class GeminiQuotaProbeTests(unittest.TestCase):
    """Probe-cache short-circuits the legacy gemini-cli after QUOTA_EXHAUSTED.

    Without the probe, gemini-cli pays ~24s of internal retry/backoff before
    falling through to the chain advance. The probe caches the reset window so
    subsequent calls skip it at the provider-function level. The probe parses
    gemini-cli's stderr, so it is scoped to GEMINI_BIN=gemini and must NOT
    suppress the default agy provider (see ProviderGeminiAgyTests).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        os.environ.pop('GEMINI_BIN', None)
        sys.modules.pop('tark_cli', None)
        self.tark = importlib.import_module('tark_cli')

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop('XDG_CACHE_HOME', None)
        os.environ.pop('GEMINI_BIN', None)

    def test_no_probe_returns_none(self):
        self.assertIsNone(self.tark._gemini_quota_probe_until())

    def test_arm_then_read_returns_future(self):
        import time as _t
        self.tark._gemini_quota_probe_set('Your quota will reset after 0h0m30s.')
        until = self.tark._gemini_quota_probe_until()
        self.assertIsNotNone(until)
        self.assertGreater(until, _t.time())
        self.assertLess(until, _t.time() + 60)

    def test_expired_probe_is_cleared(self):
        # Write a marker in the past — read should return None AND unlink.
        p = self.tark._gemini_quota_probe_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('0\n')
        self.assertIsNone(self.tark._gemini_quota_probe_until())
        self.assertFalse(p.exists())

    def test_parse_h_m_s(self):
        self.tark._gemini_quota_probe_set('reset after 1h2m3s')
        import time as _t
        until = self.tark._gemini_quota_probe_until()
        delta = until - _t.time()
        self.assertGreater(delta, 3700)  # >= 1h + 2m + 3s minus tolerance
        self.assertLess(delta, 3800)

    def test_parse_partial_window(self):
        # gemini message may say just "30m" or "45s" — both should parse.
        self.tark._gemini_quota_probe_set('reset after 30m')
        import time as _t
        until = self.tark._gemini_quota_probe_until()
        delta = until - _t.time()
        self.assertGreater(delta, 1700)
        self.assertLess(delta, 1900)

    def test_unparseable_message_falls_back_to_1h(self):
        # If gemini changes its message format, we still want a probe armed.
        self.tark._gemini_quota_probe_set('Some unrelated error text.')
        import time as _t
        until = self.tark._gemini_quota_probe_until()
        delta = until - _t.time()
        self.assertGreater(delta, 3500)
        self.assertLess(delta, 3700)

    def test_provider_gemini_short_circuits_when_probe_armed(self):
        # The probe gates the legacy gemini-cli branch only.
        os.environ['GEMINI_BIN'] = 'gemini'
        self.tark._gemini_quota_probe_set('reset after 1h')
        with mock.patch('subprocess.run', side_effect=AssertionError('must not spawn gemini')):
            verdict, tag = self.tark._provider_gemini('prompt', 'payload', 30)
        self.assertIsNone(verdict)
        self.assertEqual(tag, 'gemini-quota-cached')


class ProviderParserTests(unittest.TestCase):
    """Exercise _provider_codex / _provider_claude / _provider_gemini line
    parsing against realistic subprocess output.

    The dispatcher tests above mock at _SAFETY_PROVIDERS, so parser bugs
    (matching 'SAFEGUARDS', preamble-before-verdict, retry-success stderr)
    never trip those tests. This class patches subprocess.run instead.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        sys.modules.pop('tark_cli', None)
        self.tark = importlib.import_module('tark_cli')

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop('XDG_CACHE_HOME', None)

    @staticmethod
    def _proc(stdout='', stderr='', returncode=0):
        m = mock.Mock()
        m.stdout = stdout
        m.stderr = stderr
        m.returncode = returncode
        return m

    # --- codex --------------------------------------------------------------

    def test_codex_accepts_bare_safe(self):
        with mock.patch('subprocess.run', return_value=self._proc(stdout='SAFE\n')):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertEqual(v, 'SAFE')
        self.assertEqual(tag, 'codex-ok')

    def test_codex_accepts_unsafe_with_reason(self):
        out = "codex\nsome session header\nUNSAFE: prompt injection attempt\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertEqual(v, 'UNSAFE: prompt injection attempt')

    def test_codex_rejects_safeguards_word(self):
        # The OLD parser matched 'SAFEGUARDS' via .startswith and returned it
        # as verdict → dispatcher fail-closed at unparseable. New parser
        # requires bare SAFE or SAFE:/UNSAFE: prefix.
        out = "codex\nSAFEGUARDS are needed for this kind of request\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'codex-empty')

    def test_codex_walks_bottom_up_past_preamble(self):
        out = "codex\nHere is my assessment:\nSAFE\nthanks!\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        # "thanks!" doesn't match, "SAFE" does — bottom-up first hit wins.
        self.assertEqual(v, 'SAFE')

    def test_codex_lowercase_unsafe_with_colon(self):
        out = "codex\nunsafe: looks like injection\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertEqual(v, 'unsafe: looks like injection')

    def test_codex_rejects_unsafe_without_colon(self):
        # Bare 'UNSAFE' isn't a valid verdict per the prompt protocol — the
        # answer must be 'UNSAFE: <reason>'. Strict match.
        with mock.patch('subprocess.run', return_value=self._proc(stdout='UNSAFE\n')):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'codex-empty')

    def test_codex_rejects_safe_with_colon_annotation(self):
        # Parser/dispatcher consistency: the dispatcher uses exact-equality on
        # .upper() for the SAFE side, so "SAFE: looks fine" would be matched by
        # the parser but rejected by the dispatcher → fail-closed instead of
        # chain-advance. Parser must reject SAFE-with-colon for the chain to
        # advance to the next provider.
        with mock.patch('subprocess.run', return_value=self._proc(
            stdout='SAFE: looks fine\n',
        )):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'codex-empty')

    def test_codex_missing_binary(self):
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            v, tag = self.tark._provider_codex('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'codex-missing')

    # --- claude -------------------------------------------------------------

    def test_claude_accepts_first_line_safe(self):
        with mock.patch('subprocess.run', return_value=self._proc(stdout='SAFE\n')):
            v, tag = self.tark._provider_claude('p', 'pay', 30)
        self.assertEqual(v, 'SAFE')

    def test_claude_handles_preamble_before_verdict(self):
        # OLD parser only read line 1, so a haiku preamble would force the
        # whole chain to fail-closed. New parser walks bottom-up like codex.
        out = "Here's my assessment of the content:\n\nUNSAFE: data exfiltration attempt\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_claude('p', 'pay', 30)
        self.assertEqual(v, 'UNSAFE: data exfiltration attempt')
        self.assertEqual(tag, 'claude-ok')

    def test_claude_rejects_safeguards_word(self):
        out = "SAFEGUARDS are necessary here.\n"
        with mock.patch('subprocess.run', return_value=self._proc(stdout=out)):
            v, tag = self.tark._provider_claude('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'claude-empty')

    # --- gemini -------------------------------------------------------------

    def test_gemini_retry_success_keeps_verdict(self):
        # Critical correctness fix: when gemini retries QUOTA_EXHAUSTED and
        # eventually succeeds, the trace remains in stderr while the verdict
        # lands on stdout. We must prefer the stdout verdict — NOT discard it
        # and arm the probe.
        with mock.patch('subprocess.run', return_value=self._proc(
            stdout='SAFE\n',
            stderr='[API Error] {"code":429,"message":"QUOTA_EXHAUSTED, retrying"}',
        )):
            v, tag = self.tark._provider_gemini('p', 'pay', 30)
        self.assertEqual(v, 'SAFE')
        self.assertEqual(tag, 'gemini-ok')
        # And the probe was NOT armed.
        self.assertIsNone(self.tark._gemini_quota_probe_until())

    def test_gemini_real_quota_exhaustion_arms_probe(self):
        # Empty stdout AND quota error in stderr → arm the probe.
        with mock.patch('subprocess.run', return_value=self._proc(
            stdout='',
            stderr='QUOTA_EXHAUSTED. Your quota will reset after 1h0m0s.',
        )):
            v, tag = self.tark._provider_gemini('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'gemini-quota')
        until = self.tark._gemini_quota_probe_until()
        self.assertIsNotNone(until)

    def test_gemini_empty_stdout_no_quota_is_just_empty(self):
        # No verdict, no quota trace — chain advance without arming probe.
        with mock.patch('subprocess.run', return_value=self._proc(stdout='', stderr='')):
            v, tag = self.tark._provider_gemini('p', 'pay', 30)
        self.assertIsNone(v)
        self.assertEqual(tag, 'gemini-empty')
        self.assertIsNone(self.tark._gemini_quota_probe_until())


class ProbeAtomicityTests(unittest.TestCase):
    """The probe writer uses tmp + os.replace to keep concurrent readers from
    seeing a half-written value. Verify the .tmp doesn't leak on success and
    that the marker is readable end-to-end.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name
        sys.modules.pop('tark_cli', None)
        self.tark = importlib.import_module('tark_cli')

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop('XDG_CACHE_HOME', None)

    def test_probe_write_leaves_no_tmp_residue(self):
        self.tark._gemini_quota_probe_set('reset after 1h')
        p = self.tark._gemini_quota_probe_path()
        self.assertTrue(p.exists())
        # No .tmp left behind after successful rename.
        tmp = p.with_suffix(p.suffix + '.tmp')
        self.assertFalse(tmp.exists())

    def test_probe_repeated_writes_overwrite_cleanly(self):
        # 5 sequential arm + read cycles → marker stays valid throughout.
        for _ in range(5):
            self.tark._gemini_quota_probe_set('reset after 1h')
            self.assertIsNotNone(self.tark._gemini_quota_probe_until())


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


class FramingTableTests(unittest.TestCase):
    """All four modes (wiki/task/comment/email) must map to a distinct framing
    string so the prompt fed to providers is mode-appropriate AND the
    SHA256(model+mode+title+body) cache key collides across the Python/bash
    implementations for the same content.
    """

    def setUp(self):
        sys.modules.pop('tark_cli', None)
        self.tark = importlib.import_module('tark_cli')

    def test_all_four_modes_have_distinct_framings(self):
        framings = self.tark._SAFETY_FRAMING
        for mode in ('wiki', 'task', 'comment', 'email'):
            self.assertIn(mode, framings, f'mode {mode!r} missing from _SAFETY_FRAMING')
        self.assertEqual(len(set(framings.values())), len(framings),
                         'each mode must have a distinct framing string')

    def test_email_framing_matches_bash(self):
        # Keep in sync with safety_check.sh case statement (email branch).
        self.assertEqual(self.tark._SAFETY_FRAMING['email'], 'Email (subject + body)')

    def test_task_framing_matches_bash(self):
        # Keep in sync with safety_check.sh case statement (task branch), if/when
        # a bash implementation exists. Framing is prompt-only (not part of the
        # SHA256 cache key), so wording changes don't affect cross-impl parity.
        self.assertEqual(self.tark._SAFETY_FRAMING['task'], 'Tark task (title + description)')


class ProviderGeminiAgyTests(unittest.TestCase):
    """Subprocess-seam contract for _provider_gemini.

    The legacy `gemini` CLI retired 2026-06-18; the slot now defaults to the
    Antigravity CLI (`agy`), which has no -m flag and reads the prompt from the
    -p argv (folded prompt+payload — a stdin pipe can tip agy into agent mode).
    GEMINI_BIN=gemini restores the enterprise contract (-m + stdin).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ['XDG_CACHE_HOME'] = self._tmp.name  # empty cache => quota probe disarmed
        for k in ('GEMINI_BIN', 'SAFETY_CHECK_MODEL', 'SAFETY_CHECK_USE_API_KEYS'):
            os.environ.pop(k, None)
        for name in ('_safety_cache', 'tark_cli'):
            sys.modules.pop(name, None)
        self.tark = importlib.import_module('tark_cli')

    def tearDown(self):
        self._tmp.cleanup()
        for k in ('XDG_CACHE_HOME', 'GEMINI_BIN', 'SAFETY_CHECK_MODEL'):
            os.environ.pop(k, None)

    def _run(self, stdout='SAFE', stderr='', returncode=0):
        proc = mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)
        with mock.patch('subprocess.run', return_value=proc) as run:
            result = self.tark._provider_gemini('PROMPT-INSTR', 'PAYLOAD-BODY', 30)
        return result, run

    def test_default_bin_is_agy_folded_no_model_no_stdin(self):
        (v, tag), run = self._run(stdout='SAFE')
        self.assertEqual((v, tag), ('SAFE', 'gemini-ok'))
        argv = run.call_args[0][0]
        self.assertEqual(argv, ['agy', '-p', 'PROMPT-INSTR\n\nPAYLOAD-BODY'])
        self.assertNotIn('-m', argv)
        self.assertNotIn('input', run.call_args.kwargs)  # agy: no stdin pipe

    def test_safety_check_model_ignored_under_agy(self):
        os.environ['SAFETY_CHECK_MODEL'] = 'gemini-3.1-pro-preview'
        (_v, _t), run = self._run(stdout='SAFE')
        argv = run.call_args[0][0]
        self.assertNotIn('-m', argv)
        self.assertNotIn('gemini-3.1-pro-preview', argv)

    def test_enterprise_gemini_bin_uses_model_and_stdin(self):
        os.environ['GEMINI_BIN'] = 'gemini'
        os.environ['SAFETY_CHECK_MODEL'] = 'gemini-2.5-pro'
        (_v, _t), run = self._run(stdout='SAFE')
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], 'gemini')
        self.assertIn('-m', argv)
        self.assertIn('gemini-2.5-pro', argv)
        self.assertEqual(run.call_args.kwargs.get('input'), 'PAYLOAD-BODY')

    def test_unsafe_verdict_returned(self):
        (v, tag), _ = self._run(stdout='UNSAFE: prompt injection')
        self.assertEqual((v, tag), ('UNSAFE: prompt injection', 'gemini-ok'))

    def test_verdict_parsed_from_chatter(self):
        # agy may wrap the verdict; bottom-up parse must still find it rather
        # than return the chatter line (which would fail the dispatcher closed).
        (v, tag), _ = self._run(stdout="Here's my assessment of the text:\nSAFE")
        self.assertEqual((v, tag), ('SAFE', 'gemini-ok'))

    def test_no_clean_verdict_advances(self):
        (v, tag), _ = self._run(stdout='I cannot determine this.')
        self.assertEqual((v, tag), (None, 'gemini-empty'))

    def test_binary_missing_advances(self):
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            self.assertEqual(self.tark._provider_gemini('p', 'b', 30), (None, 'gemini-missing'))

    def test_timeout_advances(self):
        import subprocess as _sp
        with mock.patch('subprocess.run', side_effect=_sp.TimeoutExpired('agy', 30)):
            self.assertEqual(self.tark._provider_gemini('p', 'b', 30), (None, 'gemini-timeout'))

    def test_armed_quota_probe_does_not_suppress_agy(self):
        # Regression (codex review): the quota probe parses gemini-cli's stderr
        # and is gemini-cli-specific — an armed probe must NOT skip the default
        # agy provider, only the GEMINI_BIN=gemini branch.
        self.tark._gemini_quota_probe_set('Your quota will reset after 1h0m0s.')
        (v, tag), run = self._run(stdout='SAFE')
        self.assertEqual((v, tag), ('SAFE', 'gemini-ok'))
        run.assert_called_once()  # agy WAS spawned despite the armed probe

    def test_armed_quota_probe_suppresses_enterprise_gemini(self):
        os.environ['GEMINI_BIN'] = 'gemini'
        self.tark._gemini_quota_probe_set('Your quota will reset after 1h0m0s.')
        with mock.patch('subprocess.run', side_effect=AssertionError('must not spawn')):
            result = self.tark._provider_gemini('p', 'b', 30)
        self.assertEqual(result, (None, 'gemini-quota-cached'))


if __name__ == '__main__':
    unittest.main()
