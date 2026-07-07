"""AliasCanonicalizer: canonicalize entity names before ingestion.

Zep resolves entities by the names it sees in text; lexically-different
aliases ("MR-42" vs "Atlas") do not merge on their own. Zep support
recommends canonicalizing references before ingestion — either rewriting
aliases to one canonical name, or introducing the alias inline ("MR-42
(also known as Atlas)"). This transform implements both.

Ambiguous aliases are a data-corruption hazard: alias "Will" must not rewrite
the modal verb in "he will go" — and case-sensitivity alone cannot save a
word-like alias at sentence start ("Will you go?"). Pass ``risky_words`` (a
set of common words to guard against) and construction rejects aliases that
match it case-insensitively or are shorter than 3 characters; without it, no
guard runs. Per-alias replacement counts are surfaced as warnings either way,
so a runaway alias is visible in preview() before any API call.
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from typing import Literal

from zep_ingest.exceptions import ConfigurationError
from zep_ingest.types import Episode

MIN_ALIAS_CHARS = 3
MAX_NAME_CHARS = 200

#: Common English words (and word-like given names) that make catastrophic
#: aliases. Not applied by default — pass ``risky_words=DEFAULT_RISKY_WORDS``
#: (optionally ``| {"your", "words"}``) to arm the guard.
DEFAULT_RISKY_WORDS = frozenset(
    """
    the be to of and a in that have i it for not on with he as you do at this
    but his by from they we say her she or an will my one all would there
    their what so up out if about who get which go me when make can like time
    no just him know take people into year your good some could them see other
    than then now look only come its over think also back after use two how
    our work first well way even new want because any these give day most us
    is was are were been has had did says
    may might must shall should
    mark bill art page jack rob sue grant chase drew wade chuck dot pat frank
    bob don ray jim tim tom sam ben dan max leo roy guy ann joy hope faith
    """.split()
)

_URL = r"https?://\S+|www\.\S+"
_CODE_SPAN = r"`[^`]*`"


class AliasCanonicalizer:
    def __init__(
        self,
        aliases: dict[str, Sequence[str]],
        *,
        mode: Literal["rewrite", "annotate"] = "rewrite",
        strict: bool = True,
        risky_words: frozenset[str] | None = None,
    ) -> None:
        self.mode = mode
        self.strict = strict
        self.warnings: list[str] = []
        self._counts: dict[str, int] = {}
        self._alias_to_canonical: dict[str, str] = {}
        lowered_risky = (
            frozenset(w.lower() for w in risky_words) if risky_words is not None else None
        )

        for canonical, alias_list in aliases.items():
            self._validate_name(canonical, kind="canonical name")
            for alias in alias_list:
                self._validate_name(alias, kind="alias")
                if alias == canonical:
                    continue
                existing = self._alias_to_canonical.get(alias)
                if existing is not None and existing != canonical:
                    raise ConfigurationError(
                        f"Alias {alias!r} is mapped to two canonical names "
                        f"({existing!r} and {canonical!r})."
                    )
                if lowered_risky is not None:
                    self._check_risky(alias, lowered_risky)
                self._alias_to_canonical[alias] = canonical

        # One scan over aliases AND protected spans (existing canonical
        # mentions, URLs, code spans), longest literal first, so an alias that
        # contains its canonical ("Atlas Mk II" → "Atlas") still wins over the
        # protection of the bare canonical. URLs/code spans go first: an alias
        # inside them must stay untouched.
        self._canonicals = set(aliases)
        self._lowered_aliases = {a.lower(): c for a, c in self._alias_to_canonical.items()}
        # (?<!\w)/(?!\w) instead of \b: \b needs a word char on the alias side
        # of the boundary, so aliases that start or end with punctuation
        # (".NET", "C++") would silently never match.
        alias_parts = {
            a: (rf"(?<!\w){re.escape(a)}(?!\w)" if strict else rf"(?i:(?<!\w){re.escape(a)}(?!\w))")
            for a in self._alias_to_canonical
        }
        canonical_parts = {c: re.escape(c) for c in self._canonicals}
        literals = sorted({**canonical_parts, **alias_parts}.items(), key=lambda kv: -len(kv[0]))
        self._scan_pattern = (
            re.compile("|".join([_URL, _CODE_SPAN] + [part for _, part in literals]))
            if self._alias_to_canonical
            else None
        )

    @staticmethod
    def _validate_name(name: str, *, kind: str) -> None:
        if not name or not name.strip():
            raise ConfigurationError(f"Empty {kind} in alias map.")
        if len(name) > MAX_NAME_CHARS:
            raise ConfigurationError(f"{kind} too long ({len(name)} chars): {name[:50]!r}…")
        if re.search(r"[\x00-\x1f]", name):
            raise ConfigurationError(f"{kind} contains control characters: {name!r}")

    def _check_risky(self, alias: str, risky_words: frozenset[str]) -> None:
        reasons = []
        if len(alias) < MIN_ALIAS_CHARS:
            reasons.append(f"shorter than {MIN_ALIAS_CHARS} characters")
        if alias.lower() in risky_words:
            reasons.append("in your risky_words set")
        if not reasons:
            return
        raise ConfigurationError(
            f"Alias {alias!r} is {' and '.join(reasons)} — rewriting it is likely to "
            "corrupt unrelated text (note that sentence-start capitalization defeats "
            "case-sensitive matching for word-like aliases). Remove it from the alias "
            "map or from risky_words to proceed."
        )

    def apply(self, episodes: Iterable[Episode]) -> Iterator[Episode]:
        for episode in episodes:
            if episode.data_type == "json" or self._scan_pattern is None:
                yield episode
                continue
            data = self._process(episode.data)
            if data == episode.data:
                yield episode
            else:
                yield Episode(
                    data=data,
                    data_type=episode.data_type,
                    created_at=episode.created_at,
                    metadata=episode.metadata,
                    source_description=episode.source_description,
                    document=episode.document,
                )
        self.flush_warnings()

    def flush_warnings(self) -> None:
        """Move accumulated per-alias counts into ``warnings``.

        Called at the end of a fully-consumed stream, and by Pipeline before it
        collects warnings — a limited preview() leaves the generator suspended,
        so counts must not wait for exhaustion (nor leak into the next run).
        """
        for alias, count in sorted(self._counts.items()):
            canonical = self._lookup(alias)
            action = "annotated" if self.mode == "annotate" else "replaced"
            self.warnings.append(
                f'Alias "{alias}" → "{canonical}": {count} occurrence(s) {action}.'
            )
        self._counts = {}

    def _lookup(self, matched: str) -> str:
        exact = self._alias_to_canonical.get(matched)
        if exact is not None:
            return exact
        return self._lowered_aliases.get(matched.lower(), matched)

    def _process(self, text: str) -> str:
        annotated: set[str] = set()  # canonicals already annotated in this episode
        assert self._scan_pattern is not None
        pieces: list[str] = []
        last = 0
        for match in self._scan_pattern.finditer(text):
            pieces.append(text[last : match.start()])
            pieces.append(self._resolve(match.group(0), text[match.end() :], annotated))
            last = match.end()
        pieces.append(text[last:])
        return "".join(pieces)

    def _resolve(self, matched: str, following: str, annotated: set[str]) -> str:
        if matched in self._canonicals:  # protected canonical mention
            return matched
        canonical = self._lookup(matched)
        if canonical == matched:  # URL or code span
            return matched
        if self.mode == "annotate":
            if following.startswith(f" (also known as {canonical})"):
                annotated.add(canonical)  # already introduced (idempotency)
                return matched
            if canonical in annotated:
                return matched
            annotated.add(canonical)
            self._counts[matched] = self._counts.get(matched, 0) + 1
            return f"{matched} (also known as {canonical})"
        self._counts[matched] = self._counts.get(matched, 0) + 1
        return canonical
