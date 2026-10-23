"""Fixture/measurement contracts only; these are not native layout evidence."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from dashboard_layout_browser import (BASELINE_CSS, BASELINE_SHA256, BASELINE_SOURCE_COMMIT,
                                      HEADER_LAYOUT_READY, HEADER_READY_TIMEOUT_MS, horizontal_excess,
                                      read_baseline_css, replacement_headers, wait_for_header_layout)


class DashboardLayoutContracts(unittest.TestCase):
    def test_original_css_fixture_integrity_and_source(self):
        self.assertEqual(BASELINE_SOURCE_COMMIT, '8ed33ec446c4b03bf3a76ae7847957c7da58fa07')
        data = read_baseline_css()
        self.assertEqual(hashlib.sha256(data).hexdigest(), BASELINE_SHA256)
        self.assertEqual(data, BASELINE_CSS.read_bytes())
        # The repair appends narrowly-scoped overrides; legacy rules stay intact.
        current = Path(__file__).parents[1] / 'web/css/style.css'
        self.assertTrue(current.read_bytes().startswith(data))

    def test_changed_baseline_bytes_fail_before_browser_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'baseline.css'
            candidate.write_bytes(read_baseline_css() + b'\n/* modified */\n')
            with self.assertRaisesRegex(ValueError, BASELINE_SOURCE_COMMIT):
                read_baseline_css(candidate)

    def test_containment_metric_distinguishes_a_real_violation(self):
        content = {'left': 50, 'right': 250}
        self.assertEqual(horizontal_excess({'left': 60, 'right': 186}, content), 0)
        self.assertEqual(horizontal_excess({'left': 140, 'right': 266}, content), 16)
        self.assertEqual(horizontal_excess({'left': 40, 'right': 166}, content), 10)

    def test_invalid_measurement_cannot_count_as_baseline_failure(self):
        for value in (float('nan'), float('inf'), -float('inf'), None, '126'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                horizontal_excess({'left': 20, 'right': value}, {'left': 0, 'right': 390})

    def test_header_readiness_observes_all_native_transition_conditions(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is required for the DOM-only readiness predicate control')
        script = """
            const assert = require('node:assert/strict');
            const ready = """ + HEADER_LAYOUT_READY + """;
            let tags;
            global.document = {querySelectorAll(selector) {
                assert.equal(selector, '.header .el-tag'); return tags;
            }};
            global.getComputedStyle = tag => tag.style;
            function reset() {
                tags = Array.from({length: 3}, () => ({style: {transform:'none', opacity:'1'},
                    classList: ['el-tag'], getAnimations: () => []}));
            }
            reset(); assert.equal(ready(), true);
            for (const count of [0, 2, 4]) {
                reset(); tags = Array.from({length:count}, () => tags[0]);
                assert.equal(ready(), false);
            }
            for (let index=0; index<3; index++) {
                for (const [field, value] of [['transform','matrix(0.707287, 0, 0, 1, 0, 0)'],
                                               ['opacity','0.707287']]) {
                    reset(); tags[index].style[field] = value; assert.equal(ready(), false);
                }
                for (const phase of ['enter','leave']) for (const part of ['from','active','to']) {
                    reset(); tags[index].classList.push('el-zoom-in-center-'+phase+'-'+part);
                    assert.equal(ready(), false);
                }
                for (const animation of [{pending:true,playState:'idle'},
                                         {pending:false,playState:'running'}]) {
                    reset(); tags[index].getAnimations=()=>[animation]; assert.equal(ready(), false);
                }
                reset(); tags[index].getAnimations=()=>[{pending:false,playState:'finished'}];
                assert.equal(ready(), true);
            }
            console.log('readiness predicate controls passed');
        """
        result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'readiness predicate controls passed')

    def test_readiness_has_one_deadline_and_propagates_timeout_without_retry(self):
        calls = []
        failure = TimeoutError('synthetic transition never completed')
        class Page:
            def wait_for_function(self, expression, **kwargs):
                calls.append((expression, kwargs))
                raise failure
        with self.assertRaises(TimeoutError) as caught:
            wait_for_header_layout(Page())
        self.assertIs(caught.exception, failure)
        self.assertEqual(HEADER_READY_TIMEOUT_MS, 5000)
        self.assertEqual(calls, [(HEADER_LAYOUT_READY, {'timeout': 5000})])

    def test_scaled_padding_mismatch_is_not_native_overflow_evidence(self):
        # Finite algebra control, not a reconstruction of the old a031 failure.
        scale, inset = 0.7, 10
        outer = {'left': 50, 'right': 190}
        rendered_text = {'left': outer['left'] + inset * scale,
                         'right': outer['right'] - inset * scale}
        unscaled_content = {'left': outer['left'] + inset, 'right': outer['right'] - inset}
        self.assertEqual(horizontal_excess(rendered_text, unscaled_content), 3)
        self.assertEqual(horizontal_excess(rendered_text, rendered_text), 0)

    def test_fixture_body_replacement_preserves_security_headers(self):
        policy = {'cache-control': 'no-store', 'content-security-policy': "script-src 'self'",
                  'x-content-type-options': 'nosniff', 'content-type': 'text/css'}
        headers = {**policy, 'Content-Length': '999', 'etag': 'old', 'last-modified': 'old'}
        self.assertEqual(replacement_headers(headers), policy)
        self.assertEqual(headers['Content-Length'], '999', 'source response must stay unchanged')


if __name__ == '__main__':
    unittest.main()
