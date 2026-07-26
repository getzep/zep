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
        out = rewrite("PROTOTYPE-202 is on track", {"ROBOT-202": ["PROTOTYPE-202"]})
        assert out == "ROBOT-202 is on track"

    def test_word_boundary_no_substring_bleed(self):
        aliases = {"Operating System": ["OS"]}
        transform = AliasCanonicalizer(aliases, risky_words=frozenset())
        [out] = list(transform.apply([Episode(data="macOS is not just OS")]))
        assert out.data == "macOS is not just Operating System"

    def test_multiple_aliases_and_occurrences(self):
        out = rewrite(
            "PROTOTYPE-202 aka Picker X1 ships; PROTOTYPE-202 rocks",
            {"ROBOT-202": ["PROTOTYPE-202", "Picker X1"]},
        )
        assert out == "ROBOT-202 aka ROBOT-202 ships; ROBOT-202 rocks"

    def test_alias_containing_canonical_rewrites(self):
        # protecting canonical mentions must not shadow a longer alias that
        # contains the canonical (common for product/version aliases)
        out = rewrite("Project ROBOT-202 ships", {"ROBOT-202": ["Project ROBOT-202"]})
        assert out == "ROBOT-202 ships"

    def test_alias_with_canonical_prefix_rewrites(self):
        out = rewrite(
            "The ROBOT-202 shipped; PROTOTYPE-202 docs updated.",
            {"ROBOT-202": ["ROBOT-202", "PROTOTYPE-202"]},
        )
        assert out == "The ROBOT-202 shipped; ROBOT-202 docs updated."

    def test_bare_canonical_still_protected(self):
        aliases = {"ROBOT-202": ["Project ROBOT-202"]}
        out = rewrite("ROBOT-202 and Project ROBOT-202 align", aliases)
        assert out == "ROBOT-202 and ROBOT-202 align"
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
    def test_guard_is_enabled_by_default(self):
        with pytest.raises(ConfigurationError, match="risky_words"):
            AliasCanonicalizer({"William Example": ["Will"]})

    def test_supplied_risky_word_raises(self):
        with pytest.raises(ConfigurationError, match="risky_words"):
            AliasCanonicalizer({"William Example": ["Will"]}, risky_words=frozenset({"will"}))

    def test_risky_match_is_case_insensitive(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"William Example": ["WILL"]}, risky_words=frozenset({"will"}))

    def test_short_alias_raises_with_default_guard(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Sample Platform": ["W8"]})

    def test_empty_risky_words_explicitly_disables_guard(self):
        AliasCanonicalizer({"Sample Platform": ["W8"]}, risky_words=frozenset())

    def test_default_risky_words_exported_and_effective(self):
        from zep_ingest import DEFAULT_RISKY_WORDS

        with pytest.raises(ConfigurationError, match="risky_words"):
            AliasCanonicalizer({"William Example": ["Will"]}, risky_words=DEFAULT_RISKY_WORDS)

    def test_strict_mode_is_case_sensitive(self):
        transform = AliasCanonicalizer({"William Example": ["Will"]}, risky_words=None)
        [out] = list(transform.apply([Episode(data="I thought he will go. Will agreed.")]))
        assert out.data == "I thought he will go. William Example agreed."

    def test_replacement_counts_surface_in_warnings(self):
        transform = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]})
        list(transform.apply([Episode(data="PROTOTYPE-202 and PROTOTYPE-202 again")]))
        assert any("PROTOTYPE-202" in w and "2" in w for w in transform.warnings)


class TestCorrectnessEdgeCases:
    def test_idempotent_no_double_canonical(self):
        aliases = {"ROBOT-202 Program": ["ROBOT-202"]}
        text = "ROBOT-202 Program is live and ROBOT-202 too"
        once = rewrite(text, aliases)
        assert once == "ROBOT-202 Program is live and ROBOT-202 Program too"
        assert rewrite(once, aliases) == once

    def test_alias_equal_to_canonical_is_noop(self):
        out = rewrite("ROBOT-202 here", {"ROBOT-202": ["ROBOT-202"]})
        assert out == "ROBOT-202 here"

    def test_duplicate_alias_across_canonicals_raises(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"CanonA": ["PROTOTYPE-202"], "CanonB": ["PROTOTYPE-202"]})

    def test_case_folded_alias_collision_raises_in_non_strict_mode(self):
        with pytest.raises(ConfigurationError, match="(?i)case-insensitive"):
            AliasCanonicalizer(
                {"CanonA": ["PROTOTYPE-202"], "CanonB": ["prototype-202"]},
                strict=False,
            )

    def test_case_variants_for_same_canonical_are_allowed_in_non_strict_mode(self):
        transform = AliasCanonicalizer(
            {"CanonA": ["PROTOTYPE-202", "prototype-202"]},
            strict=False,
        )
        [out] = list(transform.apply([Episode(data="Prototype-202")]))
        assert out.data == "CanonA"

    def test_alias_that_is_also_a_canonical_name_raises(self):
        # "Beta" would be protected as a canonical mention, silently killing the
        # alias — reject the self-contradictory map instead
        with pytest.raises(ConfigurationError, match="Ambiguous term 'Beta'") as excinfo:
            AliasCanonicalizer({"Alpha": ["Beta"], "Beta": ["Gamma"]})
        assert "'Alpha'" in str(excinfo.value)  # both roles named, so the map is fixable
        assert "'Gamma'" in str(excinfo.value)

    def test_alias_that_is_also_a_canonical_name_raises_in_either_declaration_order(self):
        with pytest.raises(ConfigurationError, match="Ambiguous term 'Beta'"):
            AliasCanonicalizer({"Beta": ["Gamma"], "Alpha": ["Beta"]})

    def test_case_folded_alias_canonical_collision_raises_in_non_strict_mode(self):
        with pytest.raises(ConfigurationError, match="(?i)case-insensitive") as excinfo:
            AliasCanonicalizer({"Alpha": ["beta"], "Beta": ["Gamma"]}, strict=False)
        assert "'beta'" in str(excinfo.value)
        assert "'Beta'" in str(excinfo.value)

    def test_case_variant_of_a_canonical_is_a_usable_alias_in_strict_mode(self):
        # strict matching is case-sensitive, so "beta" and "Beta" are distinct terms
        out = rewrite("beta and Beta", {"Alpha": ["beta"], "Beta": ["Gamma"]})
        assert out == "Alpha and Beta"

    def test_alias_differing_only_in_case_from_its_own_canonical_is_allowed(self):
        transform = AliasCanonicalizer({"Prototype-202": ["prototype-202"]}, strict=False)
        [out] = list(transform.apply([Episode(data="prototype-202 and Prototype-202")]))
        assert out.data == "Prototype-202 and Prototype-202"

    def test_invalid_mode_raises_instead_of_rewriting(self):
        with pytest.raises(ConfigurationError, match="mode"):
            AliasCanonicalizer(
                {"ROBOT-202": ["PROTOTYPE-202"]},
                mode="annotatee",
            )

    def test_urls_not_rewritten(self):
        out = rewrite(
            "see https://example.com/PROTOTYPE-202/page and PROTOTYPE-202",
            {"ROBOT-202": ["PROTOTYPE-202"]},
        )
        assert out == "see https://example.com/PROTOTYPE-202/page and ROBOT-202"

    def test_code_spans_not_rewritten(self):
        out = rewrite(
            "run `PROTOTYPE-202` for PROTOTYPE-202",
            {"ROBOT-202": ["PROTOTYPE-202"]},
        )
        assert out == "run `PROTOTYPE-202` for ROBOT-202"

    def test_canonical_with_control_chars_raises(self):
        with pytest.raises(ConfigurationError):
            AliasCanonicalizer({"Bad\nName": ["PROTOTYPE-202"]})

    def test_json_untouched(self):
        transform = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]})
        [out] = list(transform.apply([Episode(data='{"p": "PROTOTYPE-202"}', data_type="json")]))
        assert out.data == '{"p": "PROTOTYPE-202"}'

    def test_message_episodes_processed_and_fields_kept(self):
        transform = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]})
        ep = Episode(
            data="Avery Brown: PROTOTYPE-202 shipped",
            data_type="message",
            created_at="2024-01-01T00:00:00Z",
            metadata={"channel": "general"},
        )
        [out] = list(transform.apply([ep]))
        assert out.data == "Avery Brown: ROBOT-202 shipped"
        assert out.created_at == "2024-01-01T00:00:00Z"
        assert out.metadata == {"channel": "general"}


class TestAnnotateMode:
    def test_first_mention_annotated_only(self):
        transform = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]}, mode="annotate")
        [out] = list(transform.apply([Episode(data="PROTOTYPE-202 shipped. PROTOTYPE-202 rocks.")]))
        assert out.data == "PROTOTYPE-202 (also known as ROBOT-202) shipped. PROTOTYPE-202 rocks."

    def test_annotate_safe_for_risky_alias(self):
        transform = AliasCanonicalizer(
            {"William Example": ["Will"]}, mode="annotate", risky_words=None
        )
        [out] = list(transform.apply([Episode(data="he will go; Will agreed")]))
        assert out.data == "he will go; Will (also known as William Example) agreed"

    def test_no_alias_no_change(self):
        transform = AliasCanonicalizer({"ROBOT-202": ["PROTOTYPE-202"]}, mode="annotate")
        [out] = list(transform.apply([Episode(data="nothing relevant")]))
        assert out.data == "nothing relevant"

    def test_annotate_idempotent_for_alias_containing_canonical(self):
        aliases = {"ROBOT-202": ["Project ROBOT-202"]}
        once = rewrite(
            "Project ROBOT-202 ships. Project ROBOT-202 rocks.", aliases, mode="annotate"
        )
        assert once == "Project ROBOT-202 (also known as ROBOT-202) ships. Project ROBOT-202 rocks."
        assert rewrite(once, aliases, mode="annotate") == once


class TestOneLinerIntegration:
    def test_ingest_slack_export_accepts_aliases(self, mock_zep):
        from pathlib import Path

        from zep_ingest.pipeline import ingest_slack_export

        fixture = Path(__file__).parent / "fixtures" / "slack_export"
        ingest_slack_export(
            mock_zep, fixture, graph_id="g1", aliases={"ROBOT-202": ["PROTOTYPE-202"]}
        )
        items = mock_zep.batch.add.call_args.kwargs["items"]
        joined = "\n".join(i.data for i in items)
        assert "PROTOTYPE-202" not in joined
        assert "ROBOT-202" in joined
