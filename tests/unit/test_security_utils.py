"""
PR-1 (F-002, F-003) — ironshield.utils.security.constant_time_compare

Direct unit tests for the shared helper introduced by PR-1 to fix the
timing-unsafe `!=` comparisons in both `api/server.py` (F-002) and
`agent/api.py` (F-003). See ADR-001-api-auth-centralization.md for the
design rationale (why a shared helper instead of two independent fixes).

These are Level 0 (pure unit, no I/O, no mocking needed) — the function
itself has no dependencies beyond the stdlib `secrets` module.
"""

from __future__ import annotations

from ironshield.utils.security import constant_time_compare


class TestConstantTimeCompareCorrectness:
    def test_matching_strings_return_true(self):
        assert constant_time_compare("secret123", "secret123") is True

    def test_non_matching_strings_return_false(self):
        assert constant_time_compare("wrongkey", "secret123") is False

    def test_empty_provided_against_non_empty_expected_returns_false(self):
        assert constant_time_compare("", "secret123") is False

    def test_both_empty_strings_return_true(self):
        """Matches secrets.compare_digest's own behavior for two equal,
        empty strings — not a special case in constant_time_compare
        itself, just documented here for clarity since it is a slightly
        unintuitive edge case."""
        assert constant_time_compare("", "") is True

    def test_case_sensitive(self):
        assert constant_time_compare("Secret123", "secret123") is False

    def test_prefix_match_is_not_a_match(self):
        assert constant_time_compare("secret12", "secret123") is False

    def test_different_lengths_return_false_without_raising(self):
        assert constant_time_compare("short", "a much longer expected value") is False


class TestConstantTimeCompareTypeSafety:
    """Both `provided` and `expected` are validated — not just
    `provided` — so a future caller that forgets to guarantee `expected`
    is a str (e.g. passes a None default through by mistake) gets a
    clean False instead of a TypeError from secrets.compare_digest."""

    def test_non_string_provided_returns_false_without_raising(self):
        for bad_value in (123, 123.4, True, False, None, [], {}, ["secret123"]):
            assert constant_time_compare(bad_value, "secret123") is False

    def test_non_string_expected_returns_false_without_raising(self):
        for bad_value in (123, 123.4, True, False, None, [], {}):
            assert constant_time_compare("secret123", bad_value) is False

    def test_both_non_string_returns_false_without_raising(self):
        assert constant_time_compare(None, None) is False
        assert constant_time_compare(123, 456) is False
