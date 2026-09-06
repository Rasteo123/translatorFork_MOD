"""pcluster-46: WorkAsciiChatGptApiHandler._safe_profile_segment vs
utils.settings._safe_profile_segment.

Both copies sanitize a string into a filesystem-path-safe segment, but they
diverge in behavior (alias handling, strip charset, length cap, default
value). This suite:

1. Characterizes the shared low-level primitive `sanitize_path_segment`
   (gemini_translator/utils/text_sanitize.py) directly.
2. Characterizes each caller's *current* observable behavior (including the
   divergent bits) so the wrappers keep behaving exactly as before.
3. Proves both callers route through the shared primitive (a routing test
   that must fail before the refactor and pass after).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from gemini_translator.utils.text_sanitize import sanitize_path_segment
from gemini_translator.utils import settings as settings_module
from gemini_translator.api.handlers.workascii_chatgpt import WorkAsciiChatGptApiHandler


# ---------------------------------------------------------------------------
# 1. Shared primitive
# ---------------------------------------------------------------------------

class TestSanitizePathSegment:
    def test_replaces_unsafe_chars_with_underscore(self):
        assert sanitize_path_segment("a b/c:d") == "a_b_c_d"

    def test_keeps_alnum_dash_underscore(self):
        assert sanitize_path_segment("Profile-01_test") == "Profile-01_test"

    def test_strip_chars_trims_only_given_charset(self):
        # '.' is not alnum/-/_ so the char-mapping step already turns it
        # into '_' before any stripping happens.
        assert sanitize_path_segment("_.name._", strip_chars="_") == "name"

    def test_strip_chars_can_include_dashes(self):
        assert sanitize_path_segment("_-name-_", strip_chars="_-") == "name"

    def test_max_length_truncates_after_stripping(self):
        assert sanitize_path_segment("x" * 60, max_length=48) == "x" * 48

    def test_default_used_when_result_empty(self):
        assert sanitize_path_segment("", default="fallback") == "fallback"
        assert sanitize_path_segment("___", strip_chars="_", default="fallback") == "fallback"

    def test_none_value_treated_as_empty(self):
        assert sanitize_path_segment(None, default="fallback") == "fallback"

    def test_no_strip_chars_leaves_edges_untouched(self):
        assert sanitize_path_segment("_edge_", strip_chars="") == "_edge_"


# ---------------------------------------------------------------------------
# 2. Caller-level characterization (existing behavior, incl. divergence)
# ---------------------------------------------------------------------------

class TestSettingsSafeProfileSegment:
    """utils.settings._safe_profile_segment: alias-aware, strips '._-', no
    length cap, default 'profile'."""

    @pytest.mark.parametrize("alias", ["", "default", "global", "main", "Main", "DEFAULT"])
    def test_default_aliases_collapse_to_empty(self, alias):
        assert settings_module._safe_profile_segment(alias) == ""

    def test_regular_profile_sanitized(self):
        assert settings_module._safe_profile_segment("My Profile!") == "My_Profile"

    def test_dots_are_stripped_from_edges(self):
        assert settings_module._safe_profile_segment("..weird..") == "weird"

    def test_empty_after_sanitizing_falls_back_to_profile(self):
        assert settings_module._safe_profile_segment("...") == "profile"

    def test_no_length_cap(self):
        long_name = "a" * 100
        assert settings_module._safe_profile_segment(long_name) == long_name


class TestWorkAsciiSafeProfileSegment:
    """WorkAsciiChatGptApiHandler._safe_profile_segment: no alias semantics,
    strips only '_', truncates to 48 chars, default 'default'."""

    def test_no_alias_semantics_literal_main_kept(self):
        # Divergence from utils.settings: 'main' is NOT treated as "no profile".
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("main") == "main"
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("default") == "default"

    def test_leading_trailing_dashes_are_not_stripped(self):
        # Divergence from utils.settings (which strips "._-"): here only '_'
        # is stripped, so edge dashes ('-' survives the char-mapping step
        # unlike '.') are kept.
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("--weird--") == "--weird--"
        assert settings_module._safe_profile_segment("--weird--") == "weird"

    def test_truncates_to_48_chars(self):
        long_name = "a" * 100
        result = WorkAsciiChatGptApiHandler._safe_profile_segment(long_name)
        assert result == "a" * 48

    def test_empty_falls_back_to_default(self):
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("") == "default"
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("___") == "default"

    def test_regular_value_sanitized(self):
        assert WorkAsciiChatGptApiHandler._safe_profile_segment("My Profile!") == "My_Profile"


# ---------------------------------------------------------------------------
# 3. Routing test — must FAIL before refactor, PASS after.
# ---------------------------------------------------------------------------

class TestRoutingThroughSharedPrimitive:
    def test_settings_module_routes_through_shared_primitive(self, monkeypatch):
        calls = []

        def fake_sanitize(value, **kwargs):
            calls.append((value, kwargs))
            return "SENTINEL"

        monkeypatch.setattr(settings_module, "sanitize_path_segment", fake_sanitize)
        result = settings_module._safe_profile_segment("some-profile")
        assert calls, "utils.settings._safe_profile_segment did not call sanitize_path_segment"
        assert result == "SENTINEL"

    def test_workascii_handler_routes_through_shared_primitive(self, monkeypatch):
        calls = []

        def fake_sanitize(value, **kwargs):
            calls.append((value, kwargs))
            return "SENTINEL"

        import gemini_translator.api.handlers.workascii_chatgpt as workascii_module

        monkeypatch.setattr(workascii_module, "sanitize_path_segment", fake_sanitize)
        result = WorkAsciiChatGptApiHandler._safe_profile_segment("some-workspace")
        assert calls, "WorkAsciiChatGptApiHandler._safe_profile_segment did not call sanitize_path_segment"
        assert result == "SENTINEL"
