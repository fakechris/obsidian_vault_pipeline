"""Tests for entities/aliases.py — the unioned entity_aliases view (BL-038)."""

from __future__ import annotations

import json
from pathlib import Path

from ovp_pipeline.entities.aliases import (
    KIND_AT_HANDLE,
    KIND_DISPLAY_NAME,
    KIND_EXPLICIT_ALIAS,
    KIND_GITHUB_LOGIN,
    KIND_PRIMARY,
    SOURCE_ENTITY_GITHUB_USER,
    SOURCE_ENTITY_PERSON,
    SOURCE_ENTITY_TWITTER,
    SOURCE_WHITELIST_JSONL,
    SOURCE_WHITELIST_YAML,
    build_alias_index,
    collect_entity_aliases,
)
from ovp_pipeline.entities.store import EntityStore


def _write_jsonl(path: Path, *records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_yaml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalizeAlias:
    def test_strips_at_and_lowercases(self):
        from ovp_pipeline.entities.aliases import _normalize_alias

        assert _normalize_alias("@Karpathy") == "karpathy"
        assert _normalize_alias("  @karpathy  ") == "karpathy"

    def test_empty_inputs(self):
        from ovp_pipeline.entities.aliases import _normalize_alias

        assert _normalize_alias("") == ""
        assert _normalize_alias(None) == ""


# ---------------------------------------------------------------------------
# JSONL whitelist source
# ---------------------------------------------------------------------------


class TestWhitelistJsonlSource:
    def test_emits_primary_and_at_handle(self, tmp_path):
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(jsonl, {"handle": "karpathy", "authority": 0.95})
        # Empty entity store — testing JSONL pass in isolation.
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path,
            entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        kinds = {a.alias_kind for a in out if a.canonical_handle == "karpathy"}
        assert KIND_PRIMARY in kinds
        assert KIND_AT_HANDLE in kinds
        # The @ form is the literal alias string.
        at_rows = [a for a in out if a.alias == "@karpathy"]
        assert len(at_rows) == 1
        assert at_rows[0].source == SOURCE_WHITELIST_JSONL

    def test_aliases_array_emits_explicit_alias_rows(self, tmp_path):
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(jsonl, {
            "handle": "karpathy",
            "aliases": ["andrej", "andrej karpathy", "@karpathy"],
            "authority": 0.95,
        })
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        karpathy_rows = [a for a in out if a.canonical_handle == "karpathy"]
        # Self-aliases ("@karpathy" matching the at_handle row) are
        # de-duped by the loader; "andrej" + "andrej karpathy" stay.
        explicit = [a for a in karpathy_rows if a.alias_kind == KIND_EXPLICIT_ALIAS]
        explicit_strs = {a.alias for a in explicit}
        assert explicit_strs == {"andrej", "andrej karpathy"}

    def test_skips_records_with_non_string_handle(self, tmp_path):
        # Defensive: a malformed JSONL row with handle as int / null
        # should be silently skipped, not crash the loader.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            '{"handle": 12345, "authority": 0.5}\n'
            '{"handle": null, "authority": 0.5}\n'
            '{"handle": "good", "authority": 0.9}\n',
            encoding="utf-8",
        )
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        canonicals = {a.canonical_handle for a in out}
        assert "good" in canonicals
        # Numeric / null handles never produced canonicals.
        assert 12345 not in canonicals
        assert "" not in canonicals

    def test_skips_aliases_when_not_a_list(self, tmp_path):
        # Defensive: aliases field as int / dict instead of list
        # must not raise TypeError on iteration.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            '{"handle": "alice", "aliases": 42, "authority": 0.5}\n'
            '{"handle": "bob", "aliases": {"name": "Bob"}, "authority": 0.5}\n',
            encoding="utf-8",
        )
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        # Both records still contribute their primary + at_handle rows
        # (aliases just got dropped).
        canonicals = {a.canonical_handle for a in out}
        assert {"alice", "bob"} <= canonicals

    def test_skips_blank_lines_and_comments(self, tmp_path):
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text(
            "# comment line\n"
            "\n"
            '{"handle": "karpathy", "authority": 0.95}\n',
            encoding="utf-8",
        )
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        assert any(a.canonical_handle == "karpathy" for a in out)


# ---------------------------------------------------------------------------
# YAML overrides source
# ---------------------------------------------------------------------------


class TestYamlOverridesSource:
    def test_loads_yaml_aliases(self, tmp_path):
        yaml_path = tmp_path / "60-Logs" / "author_overrides.yaml"
        _write_yaml(yaml_path, """
authors:
  - handle: "newperson"
    aliases: ["A. New Person"]
    authority: 0.78
""")
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=yaml_path,
        )
        sources = {a.source for a in out}
        assert SOURCE_WHITELIST_YAML in sources
        # Display name from aliases[] is lowercased + emitted.
        assert any(
            a.alias == "a. new person" and a.alias_kind == KIND_EXPLICIT_ALIAS
            for a in out
        )


# ---------------------------------------------------------------------------
# Entity table source
# ---------------------------------------------------------------------------


class TestEntitySource:
    def test_twitter_author_emits_primary_and_display(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="twitter_author", identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={"followers": 1500000},
            derived_authority=0.6, fetch_source="twitterapi.io",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        rows = [a for a in out if a.source == SOURCE_ENTITY_TWITTER]
        kinds_to_aliases = {a.alias_kind: a.alias for a in rows}
        assert kinds_to_aliases[KIND_PRIMARY] == "karpathy"
        assert kinds_to_aliases[KIND_AT_HANDLE] == "@karpathy"
        assert kinds_to_aliases[KIND_DISPLAY_NAME] == "andrej karpathy"

    def test_github_user_with_backlink_uses_canonical_handle(self, tmp_path):
        # apply_merge writes ``canonical_handle`` + ``canonical_entity_type``
        # back into github_user.signals.  When that back-link exists, the
        # github login becomes an alias FOR the canonical entity, not for
        # the github_user itself.  This is what makes "see karpathy on
        # github" resolve to the merged person, not to a duplicate
        # github_user record.
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        # Person entity (canonical) keyed by twitter handle.
        store.upsert(
            entity_type="person", identity_key="karpathy",
            canonical_name="Andrej Karpathy",
            signals={"links": []}, derived_authority=0.65,
            fetch_source="identity_merge",
        )
        # github_user with back-link to that person.
        store.upsert(
            entity_type="github_user", identity_key="karpathy",
            canonical_name="karpathy",
            signals={
                "canonical_handle": "karpathy",
                "canonical_entity_type": "person",
            },
            derived_authority=0.65, fetch_source="github_rest",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        # The github_login alias must point at canonical_entity_type=person.
        gh_rows = [a for a in out if a.source == SOURCE_ENTITY_GITHUB_USER]
        assert all(a.canonical_entity_type == "person" for a in gh_rows)
        assert all(a.canonical_handle == "karpathy" for a in gh_rows)
        assert any(a.alias_kind == KIND_GITHUB_LOGIN for a in gh_rows)

    def test_github_user_without_backlink_is_own_canonical(self, tmp_path):
        # Unmerged github_user — emits its own primary/at_handle/display
        # aliases as if it were a standalone entity.
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="github_user", identity_key="loneuser",
            canonical_name="Lone User", signals={"type": "User"},
            derived_authority=0.4, fetch_source="github_rest",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        gh_rows = [a for a in out if a.source == SOURCE_ENTITY_GITHUB_USER]
        assert all(a.canonical_entity_type == "github_user" for a in gh_rows)
        kinds = {a.alias_kind for a in gh_rows}
        assert KIND_PRIMARY in kinds
        assert KIND_AT_HANDLE in kinds


# ---------------------------------------------------------------------------
# Index + collision precedence
# ---------------------------------------------------------------------------


class TestBuildAliasIndex:
    def test_whitelist_beats_entity(self, tmp_path):
        # Same alias claimed by both whitelist (yaml) and twitter
        # entity — the whitelist row must win even if it has lower
        # authority.  Curated trust > derived data.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(jsonl, {"handle": "karpathy", "authority": 0.95})
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="twitter_author", identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={},
            derived_authority=0.6, fetch_source="twitterapi.io",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        index = build_alias_index(out)
        # The "karpathy" alias resolves via the whitelist row.
        assert index["karpathy"].source == SOURCE_WHITELIST_JSONL

    def test_canonical_entity_beats_bare_platform(self, tmp_path):
        # Same alias claimed by both person (canonical) and
        # twitter_author (bare platform).  Person wins.
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="twitter_author", identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={},
            derived_authority=0.5, fetch_source="twitterapi.io",
        )
        store.upsert(
            entity_type="person", identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={"links": []},
            derived_authority=0.65, fetch_source="identity_merge",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        index = build_alias_index(out)
        assert index["karpathy"].source == SOURCE_ENTITY_PERSON

    def test_higher_authority_breaks_tie(self, tmp_path):
        # Two whitelist entries (one jsonl, one yaml) both claim
        # "shared".  YAML wins by precedence (it's the editing
        # surface), but if precedence ties, higher authority wins.
        # Construct a scenario where two yaml-source rows tie and
        # the higher-authority one prevails.
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="person", identity_key="shared",
            canonical_name="Shared", signals={"links": []},
            derived_authority=0.4, fetch_source="identity_merge",
        )
        store.upsert(
            entity_type="organization", identity_key="shared",
            canonical_name="Shared Org", signals={"links": []},
            derived_authority=0.6, fetch_source="identity_merge",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        index = build_alias_index(out)
        # Person and organization tie on precedence (both 2); higher
        # authority (organization, 0.6) wins.
        chosen = index["shared"]
        assert chosen.canonical_entity_type == "organization"
        assert chosen.authority == 0.6

    def test_index_is_lowercased(self, tmp_path):
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="twitter_author", identity_key="karpathy",
            canonical_name="Andrej Karpathy", signals={},
            derived_authority=0.5, fetch_source="twitterapi.io",
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=tmp_path / "missing.jsonl",
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        index = build_alias_index(out)
        # All keys are normalized form — no "Karpathy" / "@karpathy".
        assert all(k == k.lower() for k in index)


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


class TestCanonicalSlugSafety:
    """The canonical_handle becomes both a markdown filename
    (``10-Knowledge/Entity/<handle>.md``) and a wikilink target
    (``[[<handle>]]``).  An attacker-controlled or malformed
    authors.jsonl could otherwise inject path-traversal or break
    wikilink syntax.  ``collect_entity_aliases`` filters out
    unsafe handles at the boundary."""

    def test_drops_path_traversal_canonical(self, tmp_path):
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(
            jsonl,
            {"handle": "../../../etc/passwd", "authority": 0.9},
            {"handle": "karpathy", "authority": 0.95},
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path,
            entity_store=EntityStore(
                db_path=tmp_path / "60-Logs" / "knowledge.db",
            ),
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        canonicals = {a.canonical_handle for a in out}
        assert "karpathy" in canonicals
        # The malicious handle is gone — would have escaped the
        # entity stub directory if used as `<canonical>.md`.
        assert "../../../etc/passwd" not in canonicals

    def test_drops_wikilink_breaking_chars(self, tmp_path):
        # `|`, `[`, `]`, `#`, `/` would break wikilink rendering
        # (`[[handle]]`) or yield ambiguous filenames.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(
            jsonl,
            {"handle": "evil|alias"},
            {"handle": "evil[name]"},
            {"handle": "ev/il"},
            {"handle": "evil#anchor"},
            {"handle": "valid_one"},
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path,
            entity_store=EntityStore(
                db_path=tmp_path / "60-Logs" / "knowledge.db",
            ),
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        canonicals = {a.canonical_handle for a in out}
        assert canonicals == {"valid_one"}

    def test_accepts_real_world_handle_shapes(self, tmp_path):
        # Twitter handles are alnum + underscore; github logins can
        # carry hyphens; curated whitelist allows '.' (e.g.
        # ``simon.willison``).  All must pass.
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(
            jsonl,
            {"handle": "karpathy"},
            {"handle": "op7418"},
            {"handle": "simon-willison"},
            {"handle": "simon.willison"},
            {"handle": "user_name"},
        )
        out = collect_entity_aliases(
            vault_dir=tmp_path,
            entity_store=EntityStore(
                db_path=tmp_path / "60-Logs" / "knowledge.db",
            ),
            authors_jsonl=jsonl,
            author_overrides_yaml=tmp_path / "missing.yaml",
        )
        canonicals = {a.canonical_handle for a in out}
        assert {"karpathy", "op7418", "simon-willison",
                "simon.willison", "user_name"} <= canonicals


class TestBuildAliasIndexCollisionLogging:
    """The ambiguity warning must fire whenever two sources disagree
    on canonical_handle for the same alias, regardless of which row
    wins.  Pre-fix the warning only fired on replacement — a curated
    JSONL handle that beat a conflicting entity row was silently
    accepted, hiding the conflict from operators."""

    def test_logs_when_winner_already_set(self, tmp_path, caplog):
        import logging

        # Order matters: a higher-precedence row is added FIRST so the
        # later (lower-precedence) row never replaces it.  Pre-fix
        # the warning was inside the replacement branch and never
        # fired in this scenario.
        from ovp_pipeline.entities.aliases import EntityAlias

        rows = [
            EntityAlias(
                canonical_handle="winner",
                canonical_entity_type="whitelist",
                alias="shared", alias_kind=KIND_PRIMARY,
                authority=0.9, source=SOURCE_WHITELIST_YAML,
            ),
            EntityAlias(
                canonical_handle="loser",
                canonical_entity_type="twitter_author",
                alias="shared", alias_kind=KIND_PRIMARY,
                authority=0.8, source=SOURCE_ENTITY_TWITTER,
            ),
        ]
        with caplog.at_level(logging.WARNING,
                             logger="ovp_pipeline.entities.aliases"):
            index = build_alias_index(rows)
        # Winner (yaml) is kept.
        assert index["shared"].canonical_handle == "winner"
        # Warning fired once with both sides + the winner identified.
        msgs = [r.getMessage() for r in caplog.records]
        assert any("ambiguously" in m and "winner" in m and "loser" in m
                   for m in msgs)


class TestEndToEnd:
    def test_full_union_includes_all_four_sources(self, tmp_path):
        # JSONL
        jsonl = tmp_path / "60-Logs" / "authors.jsonl"
        _write_jsonl(jsonl, {"handle": "alice", "authority": 0.9})
        # YAML
        yaml_path = tmp_path / "60-Logs" / "author_overrides.yaml"
        _write_yaml(yaml_path, """
authors:
  - handle: "bob"
    authority: 0.8
""")
        # Entity table
        store = EntityStore(db_path=tmp_path / "60-Logs" / "knowledge.db")
        store.upsert(
            entity_type="twitter_author", identity_key="charlie",
            canonical_name="Charlie", signals={},
            derived_authority=0.5, fetch_source="twitterapi.io",
        )
        store.upsert(
            entity_type="person", identity_key="dave",
            canonical_name="Dave", signals={"links": []},
            derived_authority=0.6, fetch_source="identity_merge",
        )

        out = collect_entity_aliases(
            vault_dir=tmp_path, entity_store=store,
            authors_jsonl=jsonl, author_overrides_yaml=yaml_path,
        )
        canonicals = {a.canonical_handle for a in out}
        assert {"alice", "bob", "charlie", "dave"} <= canonicals
