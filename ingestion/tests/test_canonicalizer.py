"""Tests for AliasCanonicalizer — entity canonicalization before ingestion."""

import pytest

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.transforms.canonicalizer import AliasCanonicalizer
from zep_ingest.types import Episode


def rewrite(text: str, aliases: dict, **kwargs) -> str:
    transform = AliasCanonicalizer(aliases, **kwargs)
    [out] = list(transform.apply([Episode(data=text)]))
    return out.data


class TestRewrite:
    def test_basic_alias_rewrite(self):
        out = rewrite("MR-42 is on track", {"Atlas": ["MR-42"]})
        assert out == "Atlas is on track"

    def test_word_boundary_no_substring_bleed(self):
        aliases = {"Operating System": ["OS"]}
        transform = AliasCanonicalizer(aliases)
        [out] = list(transform.apply([Episode(data="macOS is not just OS")]))
        assert out.data == "macOS is not just Operating System"

    def test_multiple_aliases_and_occurrences(self):
        out = rewrite(
            "MR-42 aka Picker X1 ships; MR-42 rocks",
            {"Atlas": ["MR-42", "Picker X1"]},
        )
        assert out == "Atlas aka Atlas ships; Atlas rocks"

    def test_alias_containing_canonical_rewrites(self):
        # protecting canonical mentions must not shadow a longer alias that
        # contains the canonical (common for product/version aliases)
        out = rewrite("Project Atlas ships", {"Atlas": ["Project Atlas"]})
        assert out == "Atlas ships"

    def test_alias_with_canonical_prefix_rewrites(self):
        out = rewrite(
            "The Atlas Mk II shipped; MR-42 docs updated.", {"Atlas": ["Atlas Mk II", "MR-42"]}
        )
        assert out == "The Atlas shipped; Atlas docs updated."

    def test_bare_canonical_still_protected(self):
        aliases = {"Atlas": ["Project Atlas"]}
        out = rewrite("Atlas and Project Atlas align", aliases)
        assert out == "Atlas and Atlas align"
        assert rewrite(out, aliases) == out

    def test_alias_with_non_word_edges_matches(self):
        # \b would silently never match aliases that start/end with punctuation
        out = rewrite("we ship .NET and C++ here", {"dotnet": [".NET"], "cpp": ["C++"]})
        assert out == "we ship dotnet and cpp here"

    def test_longest_alias_wins(self):
        out = rewrite(
            "alpha beta gamma done",
            {"CanonLong": ["alpha beta gamma"], "CanonShort": ["alpha beta"]},
        )
        assert out == "CanonLong done"


class TestRiskyAliasGuard:
    def test_no_guard_by_default(self):
        transform = AliasCanonicalizer({"Will Hughes": ["Will"]})
        assert transform.warnings == []

    def test_supplied_risky_word_raises(self):
        with pytest.raises(ConfigurationError, match="risky_words"):
            AliasCanonicalizer({"Will Hughes": ["Will"]}, risky_words=frozenset({"will"}))

    def test_risky_match_is_case_insensitive(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Will Hughes": ["WILL"]}, risky_words=frozenset({"will"}))

    def test_short_alias_raises_only_with_guard(self):
        AliasCanonicalizer({"Windows 8": ["W8"]})
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Windows 8": ["W8"]}, risky_words=frozenset({"the"}))

    def test_empty_risky_words_still_arms_length_check(self):
        # an explicitly-passed empty set means "guard on, no extra words"
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Windows 8": ["W8"]}, risky_words=frozenset())

    def test_default_risky_words_exported_and_effective(self):
        from zep_ingest import DEFAULT_RISKY_WORDS

        with pytest.raises(ConfigurationError, match="risky_words"):
            AliasCanonicalizer({"Will Hughes": ["Will"]}, risky_words=DEFAULT_RISKY_WORDS)

    def test_strict_mode_is_case_sensitive(self):
        transform = AliasCanonicalizer({"Will Hughes": ["Will"]})
        [out] = list(transform.apply([Episode(data="I thought he will go. Will agreed.")]))
        assert out.data == "I thought he will go. Will Hughes agreed."

    def test_replacement_counts_surface_in_warnings(self):
        transform = AliasCanonicalizer({"Atlas": ["MR-42"]})
        list(transform.apply([Episode(data="MR-42 and MR-42 again")]))
        assert any("MR-42" in w and "2" in w for w in transform.warnings)


class TestCorrectnessEdgeCases:
    def test_idempotent_no_double_canonical(self):
        aliases = {"Atlas Program": ["Atlas"]}
        text = "Atlas Program is live and Atlas too"
        once = rewrite(text, aliases)
        assert once == "Atlas Program is live and Atlas Program too"
        assert rewrite(once, aliases) == once

    def test_alias_equal_to_canonical_is_noop(self):
        out = rewrite("Atlas here", {"Atlas": ["Atlas"]})
        assert out == "Atlas here"

    def test_duplicate_alias_across_canonicals_raises(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"CanonA": ["MR-42"], "CanonB": ["MR-42"]})

    def test_urls_not_rewritten(self):
        out = rewrite(
            "see https://example.com/MR-42/page and MR-42",
            {"Atlas": ["MR-42"]},
        )
        assert out == "see https://example.com/MR-42/page and Atlas"

    def test_code_spans_not_rewritten(self):
        out = rewrite("run `MR-42` for MR-42", {"Atlas": ["MR-42"]})
        assert out == "run `MR-42` for Atlas"

    def test_canonical_with_control_chars_raises(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Bad\nName": ["MR-42"]})

    def test_json_untouched(self):
        transform = AliasCanonicalizer({"Atlas": ["MR-42"]})
        [out] = list(transform.apply([Episode(data='{"p": "MR-42"}', data_type="json")]))
        assert out.data == '{"p": "MR-42"}'

    def test_message_episodes_processed_and_fields_kept(self):
        transform = AliasCanonicalizer({"Atlas": ["MR-42"]})
        ep = Episode(
            data="Alice: MR-42 shipped",
            data_type="message",
            created_at="2024-01-01T00:00:00Z",
            metadata={"channel": "general"},
        )
        [out] = list(transform.apply([ep]))
        assert out.data == "Alice: Atlas shipped"
        assert out.created_at == "2024-01-01T00:00:00Z"
        assert out.metadata == {"channel": "general"}


class TestAnnotateMode:
    def test_first_mention_annotated_only(self):
        transform = AliasCanonicalizer({"Atlas": ["MR-42"]}, mode="annotate")
        [out] = list(transform.apply([Episode(data="MR-42 shipped. MR-42 rocks.")]))
        assert out.data == "MR-42 (also known as Atlas) shipped. MR-42 rocks."

    def test_annotate_safe_for_risky_alias(self):
        transform = AliasCanonicalizer({"Will Hughes": ["Will"]}, mode="annotate")
        [out] = list(transform.apply([Episode(data="he will go; Will agreed")]))
        assert out.data == "he will go; Will (also known as Will Hughes) agreed"

    def test_no_alias_no_change(self):
        transform = AliasCanonicalizer({"Atlas": ["MR-42"]}, mode="annotate")
        [out] = list(transform.apply([Episode(data="nothing relevant")]))
        assert out.data == "nothing relevant"

    def test_annotate_idempotent_for_alias_containing_canonical(self):
        aliases = {"Atlas": ["Project Atlas"]}
        once = rewrite("Project Atlas ships. Project Atlas rocks.", aliases, mode="annotate")
        assert once == "Project Atlas (also known as Atlas) ships. Project Atlas rocks."
        assert rewrite(once, aliases, mode="annotate") == once


class TestOneLinerIntegration:
    def test_ingest_slack_export_accepts_aliases(self, mock_zep):
        from pathlib import Path

        from zep_ingest.pipeline import ingest_slack_export

        fixture = Path(__file__).parent / "fixtures" / "slack_export"
        ingest_slack_export(mock_zep, fixture, graph_id="g1", aliases={"Atlas": ["MR-42"]})
        items = mock_zep.batch.add.call_args.kwargs["items"]
        joined = "\n".join(i.data for i in items)
        assert "MR-42" not in joined
        assert "Atlas" in joined
