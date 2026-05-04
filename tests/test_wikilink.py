"""Tests for entities/wikilink.py — auto-wikilink for evergreen prose (BL-040)."""

from __future__ import annotations

from ovp_pipeline.entities.aliases import (
    KIND_AT_HANDLE,
    KIND_DISPLAY_NAME,
    KIND_PRIMARY,
    SOURCE_ENTITY_PERSON,
    SOURCE_WHITELIST_JSONL,
    EntityAlias,
)
from ovp_pipeline.entities.wikilink import (
    _find_skip_regions,
    apply_wikilinks,
    build_alias_pattern,
    ensure_entity_stub_files,
)


def _alias(canonical: str, alias: str, *, kind: str = KIND_PRIMARY,
           etype: str = "person", authority: float = 0.6,
           source: str = SOURCE_ENTITY_PERSON) -> EntityAlias:
    return EntityAlias(
        canonical_handle=canonical,
        canonical_entity_type=etype,
        alias=alias,
        alias_kind=kind,
        authority=authority,
        source=source,
    )


def _build_index(*aliases: EntityAlias) -> dict[str, EntityAlias]:
    return {a.alias: a for a in aliases}


# ---------------------------------------------------------------------------
# Skip regions
# ---------------------------------------------------------------------------


class TestFindSkipRegions:
    def test_frontmatter_at_top(self):
        text = "---\nslug: x\n---\nbody karpathy\n"
        skip = _find_skip_regions(text)
        # Frontmatter spans from 0 to the closing ``---\n``.
        assert any(s == 0 for s, _ in skip)
        # The "body" portion is NOT in any skip range.
        body_start = text.index("body")
        assert not any(s <= body_start < e for s, e in skip)

    def test_fenced_code_block(self):
        text = "before\n```\nkarpathy\n```\nafter karpathy"
        skip = _find_skip_regions(text)
        # The fenced block is in skip; the second "karpathy" (after) is not.
        first_kp = text.index("karpathy")
        second_kp = text.rindex("karpathy")
        assert any(s <= first_kp < e for s, e in skip)
        assert not any(s <= second_kp < e for s, e in skip)

    def test_inline_code(self):
        text = "see `karpathy` and karpathy"
        skip = _find_skip_regions(text)
        first_kp = text.index("karpathy")
        second_kp = text.rindex("karpathy")
        assert any(s <= first_kp < e for s, e in skip)
        assert not any(s <= second_kp < e for s, e in skip)

    def test_existing_wikilink(self):
        text = "[[karpathy]] and karpathy"
        skip = _find_skip_regions(text)
        first = text.index("[[")
        # The whole [[karpathy]] is skipped.
        assert any(s <= first < e for s, e in skip)
        # The bare second "karpathy" is not.
        second = text.rindex("karpathy")
        assert not any(s <= second < e for s, e in skip)

    def test_existing_markdown_link(self):
        text = "see [Andrej](https://x.com/karpathy) here"
        skip = _find_skip_regions(text)
        # The whole `[Andrej](...)` is skipped.
        link_start = text.index("[Andrej]")
        link_end = text.index(")") + 1
        assert any(s <= link_start and link_end <= e for s, e in skip)


# ---------------------------------------------------------------------------
# Pattern building + replacement
# ---------------------------------------------------------------------------


class TestBuildAliasPattern:
    def test_empty_index_never_matches(self):
        p = build_alias_pattern({})
        assert p.search("any text") is None

    def test_word_boundary_left_blocks_partial_prefix(self):
        # "foo@karpathy" should NOT match the alias "karpathy" because
        # the @ is glued to a word char on the left.
        index = _build_index(_alias("karpathy", "karpathy"))
        p = build_alias_pattern(index)
        assert p.search("foo@karpathy") is None

    def test_at_handle_left_boundary(self):
        # "@karpathy" by itself MUST match the at_handle alias.
        index = _build_index(_alias("karpathy", "@karpathy", kind=KIND_AT_HANDLE))
        p = build_alias_pattern(index)
        assert p.search("see @karpathy") is not None

    def test_word_boundary_right_blocks_plural(self):
        # "karpathys" should NOT match "karpathy" — would corrupt
        # legitimate prose.
        index = _build_index(_alias("karpathy", "karpathy"))
        p = build_alias_pattern(index)
        assert p.search("karpathys") is None

    def test_longest_match_wins(self):
        # "andrej karpathy" must beat "andrej" when both are aliases.
        index = _build_index(
            _alias("karpathy", "andrej karpathy", kind=KIND_DISPLAY_NAME),
            _alias("andrej-other", "andrej"),
        )
        p = build_alias_pattern(index)
        m = p.search("see andrej karpathy talk")
        assert m is not None
        assert m.group(0).lower() == "andrej karpathy"


class TestApplyWikilinks:
    def test_simple_replacement_uses_short_form(self):
        # When the matched text is the canonical handle, no alias
        # piping needed.
        index = _build_index(_alias("karpathy", "karpathy"))
        result = apply_wikilinks("see karpathy talk", index)
        assert result.text == "see [[karpathy]] talk"
        assert result.n_replaced == 1
        assert result.canonicals_used == {"karpathy"}

    def test_explicit_alias_uses_pipe_form(self):
        # When the matched text differs from the canonical, preserve
        # the prose surface via the |alias form.  Use an
        # explicit_alias (curated, on the safe-by-default kinds list)
        # rather than a display_name (default-excluded post-fix).
        from ovp_pipeline.entities.aliases import KIND_EXPLICIT_ALIAS

        index = _build_index(
            _alias("karpathy", "karpathy"),
            _alias("karpathy", "andrej karpathy", kind=KIND_EXPLICIT_ALIAS),
        )
        result = apply_wikilinks("Andrej Karpathy said it", index)
        # Original case preserved inside the wikilink.
        assert "[[karpathy|Andrej Karpathy]]" in result.text
        assert result.n_replaced == 1

    def test_at_handle_collapses_to_short_form(self):
        # ``@karpathy`` and ``karpathy`` normalize to the same lookup
        # key, so both prose forms produce the short ``[[karpathy]]``
        # wikilink.  The ``@`` cue is dropped in favor of consistency
        # with non-@ mentions of the same entity.  In Obsidian both
        # render identically; keeping the short form makes the
        # operation idempotent + the markdown cleaner.
        index = _build_index(
            _alias("karpathy", "karpathy"),
            _alias("karpathy", "@karpathy", kind=KIND_AT_HANDLE),
        )
        result = apply_wikilinks("contact @karpathy for details", index)
        assert "[[karpathy]]" in result.text
        assert result.n_replaced == 1

    def test_skips_inside_code_block(self):
        index = _build_index(_alias("karpathy", "karpathy"))
        text = (
            "before karpathy\n"
            "```\n"
            "karpathy\n"
            "```\n"
            "after karpathy"
        )
        result = apply_wikilinks(text, index)
        # Two replacements (before + after); the one inside ```...``` stays.
        assert result.n_replaced == 2
        assert "```\nkarpathy\n```" in result.text

    def test_skips_inside_existing_wikilink(self):
        index = _build_index(_alias("karpathy", "karpathy"))
        text = "[[karpathy]] and karpathy"
        result = apply_wikilinks(text, index)
        # First occurrence stays (already linked), second gets linked.
        assert result.n_replaced == 1
        assert result.text == "[[karpathy]] and [[karpathy]]"

    def test_skips_frontmatter(self):
        index = _build_index(_alias("karpathy", "karpathy"))
        text = "---\nslug: foo\nauthor: karpathy\n---\nbody karpathy"
        result = apply_wikilinks(text, index)
        # Replacement only happens in body.
        assert result.n_replaced == 1
        # Frontmatter "karpathy" is intact.
        assert "author: karpathy" in result.text

    def test_skips_existing_markdown_link(self):
        index = _build_index(_alias("karpathy", "karpathy"))
        text = "see [profile](https://x.com/karpathy) and karpathy"
        result = apply_wikilinks(text, index)
        # Markdown link untouched; bare "karpathy" linked.
        assert "[profile](https://x.com/karpathy)" in result.text
        assert result.n_replaced == 1

    def test_idempotent(self):
        # Running twice on the same input == running once.
        index = _build_index(
            _alias("karpathy", "karpathy"),
            _alias("karpathy", "andrej karpathy", kind=KIND_DISPLAY_NAME),
        )
        original = "Andrej Karpathy and karpathy walked into a bar."
        once = apply_wikilinks(original, index).text
        twice = apply_wikilinks(once, index).text
        assert once == twice

    def test_empty_index_is_no_op(self):
        result = apply_wikilinks("karpathy", {})
        assert result.n_replaced == 0
        assert result.text == "karpathy"

    def test_display_name_filtered_by_default(self):
        # The bug from the real-vault smoke run: a github_user with
        # canonical_name "Image" auto-derives a display_name alias
        # of "image" that would link every occurrence of the
        # English word.  Default behavior MUST exclude display_names.
        from ovp_pipeline.entities.aliases import KIND_DISPLAY_NAME

        index = _build_index(
            _alias("image1", "image1"),
            _alias("image1", "image", kind=KIND_DISPLAY_NAME),
        )
        result = apply_wikilinks("see image generation wrapper", index)
        # The English word stays untouched.
        assert "[[image" not in result.text
        assert result.text == "see image generation wrapper"

    def test_display_name_opt_in_via_kinds(self):
        # When the caller explicitly opts in to display_name linking,
        # the rewrite happens.
        from ovp_pipeline.entities.aliases import (
            KIND_DISPLAY_NAME,
            KIND_PRIMARY,
        )

        index = _build_index(
            _alias("image1", "image1"),
            _alias("image1", "image", kind=KIND_DISPLAY_NAME),
        )
        result = apply_wikilinks(
            "see image generation wrapper", index,
            kinds=frozenset({KIND_PRIMARY, KIND_DISPLAY_NAME}),
        )
        assert "[[image1|image]]" in result.text

    def test_short_alias_filtered_by_min_length(self):
        # "ai" is 2 chars — below the default min_length of 3.  Even
        # if it's a real entity, auto-linking would corrupt every
        # English mention of "AI".
        index = _build_index(_alias("openai", "ai"))
        result = apply_wikilinks("the ai industry boomed", index)
        assert "[[" not in result.text

    def test_min_length_can_be_overridden(self):
        index = _build_index(_alias("openai", "ai"))
        result = apply_wikilinks(
            "the ai industry boomed", index, min_length=2,
        )
        # Lowering the floor lets the 2-char alias through.
        assert result.n_replaced == 1

    def test_cjk_alias_roundtrips(self):
        # CJK alias as an explicit_alias (e.g., from authors.jsonl
        # ``aliases: ["歸藏"]``) — must match correctly even though
        # the prose has no whitespace boundary in the Western sense.
        # Curated explicit_alias is on the safe-by-default kinds list.
        from ovp_pipeline.entities.aliases import KIND_EXPLICIT_ALIAS

        index = _build_index(
            _alias("op7418", "op7418"),
            _alias("op7418", "歸藏", kind=KIND_EXPLICIT_ALIAS),
        )
        result = apply_wikilinks("歸藏 写过这个", index)
        assert "[[op7418|歸藏]]" in result.text
        assert result.n_replaced == 1


# ---------------------------------------------------------------------------
# Stub generation
# ---------------------------------------------------------------------------


class TestEnsureEntityStubFiles:
    def test_creates_stub_for_missing_canonical(self, tmp_path):
        rep = {
            "karpathy": _alias("karpathy", "karpathy"),
        }
        created = ensure_entity_stub_files(tmp_path, rep)
        assert len(created) == 1
        body = created[0].read_text(encoding="utf-8")
        assert "slug: karpathy" in body
        assert "entity_type: person" in body
        assert "stub: true" in body
        assert "ovp-link-entities" in body

    def test_does_not_overwrite_existing(self, tmp_path):
        # Pre-create a curated entity page; the stub generator must
        # NOT touch it (would silently overwrite the user's content).
        entity_dir = tmp_path / "10-Knowledge" / "Entity"
        entity_dir.mkdir(parents=True)
        existing = entity_dir / "karpathy.md"
        existing.write_text("CURATED CONTENT — DO NOT TOUCH", encoding="utf-8")

        rep = {"karpathy": _alias("karpathy", "karpathy")}
        created = ensure_entity_stub_files(tmp_path, rep)
        assert created == []
        # Existing content untouched.
        assert existing.read_text(encoding="utf-8") == "CURATED CONTENT — DO NOT TOUCH"

    def test_dry_run_does_not_write(self, tmp_path):
        rep = {"karpathy": _alias("karpathy", "karpathy")}
        created = ensure_entity_stub_files(tmp_path, rep, dry_run=True)
        # Reports the path but doesn't create it.
        assert len(created) == 1
        assert not created[0].exists()

    def test_renders_authority_when_present(self, tmp_path):
        rep = {"karpathy": _alias("karpathy", "karpathy", authority=0.65)}
        ensure_entity_stub_files(tmp_path, rep)
        body = (tmp_path / "10-Knowledge" / "Entity" / "karpathy.md").read_text(
            encoding="utf-8",
        )
        assert "authority: 0.65" in body

    def test_omits_authority_when_none(self, tmp_path):
        rep = {"foo": EntityAlias(
            canonical_handle="foo", canonical_entity_type="whitelist",
            alias="foo", alias_kind=KIND_PRIMARY, authority=None,
            source=SOURCE_WHITELIST_JSONL,
        )}
        ensure_entity_stub_files(tmp_path, rep)
        body = (tmp_path / "10-Knowledge" / "Entity" / "foo.md").read_text(
            encoding="utf-8",
        )
        assert "authority:" not in body
