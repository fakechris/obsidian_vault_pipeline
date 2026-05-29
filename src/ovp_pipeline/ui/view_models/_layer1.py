# BL-110: extracted from ui/view_models.py — verbatim move, no logic change.
# ruff: noqa: F401, F403, F405  # deliberate package re-export shim (BL-110).
from __future__ import annotations

from ._constants import *
from ._layer0 import *




def _activity_item_identity(
    state: str, slug: str, payload: dict[str, Any]
) -> str | None:
    """Stable distinct-count identity for one audit row under a
    given Activity card, or None when the row carries no usable
    identity (then it is shown in the drilldown but counted by
    neither side — so card count == drilldown distinct count holds
    by construction).

    Identity kind per state (BL-101): source slug for
    Received/Extracted/NeedsAction, object id for Accepted, cluster
    id for Synthesized.  ``min()`` picks a deterministic
    representative when a payload carries several.
    """
    kind = _ACTIVITY_IDENTITY_KIND.get(state, "source")
    if kind == "object":
        ids = audit_object_ids(payload)
        if ids:
            return min(ids)
        # promote rows that only carry a source fall back to the
        # source identity so they still count once.
        return _source_identity(slug, payload)
    if kind == "cluster":
        ids = audit_cluster_ids(payload)
        return min(ids) if ids else None
    return _source_identity(slug, payload)



def _briefing_value_check(first_useful_sign: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(first_useful_sign, dict):
        return {
            "status": "empty",
            "kind": "",
            "reason": "No background insight or priority item has enough current evidence to surface.",
            "evidence_count": 0,
            "actionability": "review",
        }
    raw_evidence = first_useful_sign.get("evidence_count")
    try:
        evidence_count = int(raw_evidence)
    except (TypeError, ValueError):
        evidence_count = _briefing_value_evidence_count(first_useful_sign)
    raw_actionability = first_useful_sign.get("actionability")
    actionability = (
        str(raw_actionability).strip()
        if str(raw_actionability or "").strip()
        else _briefing_value_actionability(first_useful_sign)
    )
    kind = str(first_useful_sign.get("kind") or "")
    title = str(first_useful_sign.get("title") or "")
    return {
        "status": "useful",
        "kind": kind,
        "reason": (
            f"{title or kind} surfaced with {evidence_count} evidence reference(s) "
            f"and {actionability} follow-up."
        ),
        "evidence_count": evidence_count,
        "actionability": actionability,
    }



def _build_dashboard_workflow_groups(
    *,
    requested_pack: str,
    research_overview_supported: bool,
) -> list[dict[str, Any]]:
    return [
        _workflow_group(
            "orient",
            "Orient",
            "Start with the compiled entry products before diving into individual queues.",
            [
                {
                    "label": "Orientation Brief",
                    "path": _scoped_path("/ops/briefing", pack_name=requested_pack),
                    "detail": "Read the current entry product.",
                },
                {
                    "label": "Workbench Home",
                    "path": _scoped_path("/", pack_name=requested_pack),
                    "detail": "Return to the current shell overview.",
                },
            ],
        ),
        _workflow_group(
            "inspect",
            "Inspect",
            "Read the current knowledge state directly from compiled browsing surfaces.",
            [
                {
                    "label": "Objects",
                    "path": _scoped_path("/ops/objects", pack_name=requested_pack),
                    "detail": "Browse indexed evergreen objects.",
                },
                {
                    "label": "Search",
                    "path": _scoped_path("/search", pack_name=requested_pack),
                    "detail": "Search notes and objects across the shell.",
                },
            ],
        ),
        _workflow_group(
            "review",
            "Review",
            "Open the highest-signal maintenance surfaces for contradictions, summaries, and signals.",
            [
                {
                    "label": "Signals",
                    "path": _scoped_path("/ops/signals", pack_name=requested_pack),
                    "detail": "Review current active signals.",
                },
                {
                    "label": "Contradictions" if research_overview_supported else "Actions",
                    "path": _scoped_path(
                        "/ops/contradictions" if research_overview_supported else "/ops/actions",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Inspect open semantic tensions."
                        if research_overview_supported
                        else "Review queued execution actions."
                    ),
                },
            ],
        ),
        _workflow_group(
            "trace",
            "Trace",
            "Follow provenance and downstream production chains before editing or reviewing.",
            [
                {
                    "label": "Production",
                    "path": _scoped_path("/ops/production", pack_name=requested_pack),
                    "detail": "Inspect production weak points and chain state.",
                },
                {
                    "label": "Notes",
                    "path": _scoped_path(
                        "/ops/objects",
                        pack_name=requested_pack,
                    ),
                    "detail": "Use object pages as the primary trace surface.",
                },
            ],
        ),
        _workflow_group(
            "explore",
            "Explore",
            "Move through topic, graph, and timeline surfaces once the shell has oriented you.",
            [
                {
                    "label": "Events" if research_overview_supported else "Objects",
                    "path": _scoped_path(
                        "/ops/events" if research_overview_supported else "/ops/objects",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Explore timeline and dossier surfaces."
                        if research_overview_supported
                        else "Explore the shared-shell object browser."
                    ),
                },
                {
                    "label": "Clusters" if research_overview_supported else "Search",
                    "path": _scoped_path(
                        "/ops/clusters" if research_overview_supported else "/search",
                        pack_name=requested_pack,
                    ),
                    "detail": (
                        "Explore graph clusters and higher-order structure."
                        if research_overview_supported
                        else "Use search to move laterally through the vault."
                    ),
                },
            ],
        ),
    ]



def _build_evolution_section(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    link_type: str | None = None,
    status: str = "candidate",
    scoped_object_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in (scoped_object_ids or []) if object_id))
    canonical_paths = {
        path
        for path in _object_scope_paths(
            vault_dir,
            normalized_object_ids,
            pack_name=pack_name,
        ).values()
        if path
    }
    reviewed_links = list_evolution_links(
        vault_dir,
        object_ids=normalized_object_ids or None,
        pack_name=pack_name,
        query=query,
        link_type=link_type,
    )
    reviewed_evolution_ids = {str(item["evolution_id"]) for item in reviewed_links}
    accepted_links = [item for item in reviewed_links if item["status"] == "accepted"]
    rejected_links = [item for item in reviewed_links if item["status"] == "rejected"]
    candidate_items = [
        item
        for item in list_evolution_candidates(
            vault_dir,
            object_ids=normalized_object_ids or None,
            pack_name=pack_name,
            query=query,
            link_type=link_type,
            status="candidate",
        )
        if item["evolution_id"] not in reviewed_evolution_ids
    ]
    if normalized_object_ids:
        filtered_items: list[dict[str, Any]] = []
        for item in candidate_items:
            refs = (str(item["earlier_ref"]), str(item["later_ref"]))
            if item["subject_kind"] == "object" and item["subject_id"] in normalized_object_ids:
                filtered_items.append(item)
                continue
            if any(
                ref.startswith(f"claim://{object_id}::") or ref == f"object://{object_id}"
                for object_id in normalized_object_ids
                for ref in refs
            ):
                filtered_items.append(item)
                continue
            if any(path in canonical_paths for path in item["source_paths"]):
                filtered_items.append(item)
        candidate_items = filtered_items
        accepted_links = [
            item for item in accepted_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
        rejected_links = [
            item for item in rejected_links
            if set(item.get("object_ids", [])).intersection(normalized_object_ids)
        ]
    if status == "accepted":
        candidate_items = []
    elif status == "rejected":
        candidate_items = []
        accepted_links = []
    elif status == "candidate":
        pass
    else:
        # keep all sections visible on the default "all" view
        status = "all"
    return {
        "accepted_links": accepted_links,
        "rejected_links": rejected_links,
        "candidate_items": candidate_items,
        "candidate_count": len(candidate_items),
        "accepted_count": len(accepted_links),
        "rejected_count": len(rejected_links),
        "link_types": sorted(
            {
                *(item["link_type"] for item in candidate_items),
                *(str(item.get("link_type") or "") for item in accepted_links),
                *(str(item.get("link_type") or "") for item in rejected_links),
            }
        ),
        "status": status,
    }



def _build_latest_digest_info(
    vault_dir: Path, *, requested_pack: str
) -> dict[str, str]:
    """Look up the most recent file under
    ``40-Resources/Generated/digests/`` and return a small dict the
    Reader home banner card consumes.  Empty dict when the folder
    is missing or empty."""
    folder = Path(vault_dir) / "40-Resources" / "Generated" / "digests"
    if not folder.exists():
        return {}
    candidates = sorted(folder.glob("*.md"))
    if not candidates:
        return {}
    latest = candidates[-1]
    # The task dispatcher writes ``YYYY-MM-DD-<prefix>-<slug>.md``
    # (e.g. ``2026-05-11-digest-daily.md``).  Use the first 10
    # characters of the filename to recover the date label; the
    # earlier ``latest.stem`` extraction shipped the whole name into
    # the home banner (rev-bot 206.1).
    date_str = latest.name[:10]
    # Teaser: skip the YAML frontmatter block and the H1 heading,
    # then return the first non-blank paragraph of the digest body.
    # Earlier this loop skipped any line starting with ``---`` or
    # ``#`` individually, which kept it inside the frontmatter and
    # returned ``type: digest`` as the teaser for every newly
    # generated digest (Codex P2 / rev-bot 206 follow-up).
    teaser = ""
    try:
        body = latest.read_text(encoding="utf-8")
        lines = body.splitlines()
        # Strip the leading frontmatter block (--- ... ---) if present.
        if lines and lines[0].strip() == "---":
            try:
                close_idx = next(
                    i for i, line in enumerate(lines[1:], start=1)
                    if line.strip() == "---"
                )
                lines = lines[close_idx + 1:]
            except StopIteration:
                # Malformed frontmatter — bail to empty teaser.
                lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            teaser = stripped
            break
        if len(teaser) > 220:
            teaser = teaser[:217].rstrip() + "…"
    except OSError:
        teaser = ""
    rel = str(latest.relative_to(vault_dir))
    href = _scoped_path(
        f"/note?path={quote(rel, safe='')}",
        pack_name=requested_pack,
    )
    # BL-119: honest "today" comparison.  Pre-fix the renderer
    # hardcoded "Today's digest" regardless of whether the latest
    # file's date actually matched today — so a vault browsed at
    # 04:00 before the 06:00 LaunchAgent fires showed yesterday's
    # digest labelled as today's.  Operator-local date matches what
    # the LaunchAgent uses to filename the file at 06:00 (the file
    # for ``YYYY-MM-DD`` lands during that day's morning slot).
    from datetime import datetime
    today_local = datetime.now().strftime("%Y-%m-%d")
    return {
        "date": date_str,
        "href": href,
        "teaser": teaser,
        "is_today": date_str == today_local,
    }



def _build_note_jump_path(path: object, *, pack_name: str | None = None) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    return _scoped_path(f"/note?path={quote(normalized, safe='')}", pack_name=pack_name)



def _compute_v2_lineage(
    vault_dir: Path | str,
    note_path: str,
    requested_pack: str,
) -> dict[str, Any] | None:
    """Compute the BL-058 raw-source ↔ evergreens ↔ crystals chain
    for the note at ``note_path``.

    Pre-fix the only lineage signal was ``production_chain`` (the
    legacy deep-dive era data flow).  v2 evergreens come from raw
    GitHub/article sources in ``50-Inbox/03-Processed`` rather than
    deep-dives, so the operator had no UI to answer "which raw
    source did this evergreen come from?" or "which evergreens
    came from this raw source?".  This payload fills that gap.

    Returns ``None`` when the note isn't an evergreen or a raw
    intake source — the caller suppresses the card in that case
    so non-applicable notes (MOCs, atlas pages, …) don't render
    an empty section.

    Shape::

        {
          "kind": "evergreen" | "raw_source",
          "raw_source": {"slug", "path", "note_href"} | None,
          "evergreens": [{slug, title, note_href}, ...],
          "clusters": [{cluster_id, label, member_count, cluster_href}, ...],
          "crystals": [{kind, crystal_id, label, note_href}, ...],
        }
    """
    rel = str(note_path).replace("\\", "/").lstrip("./")
    is_evergreen = rel.startswith("10-Knowledge/Evergreen/") and rel.endswith(".md")
    is_raw_source = rel.startswith("50-Inbox/03-Processed/") and rel.endswith(".md")
    if not (is_evergreen or is_raw_source):
        return None

    vault_root = Path(vault_dir).resolve()
    abs_path = vault_root / rel
    if not abs_path.exists():
        return None

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return None

    raw_stem: str | None = None
    target_evergreen_slugs: list[str] = []

    if is_evergreen:
        # Parse the body's ``## Source`` block to find the raw source
        # wikilink.  Frontmatter doesn't yet carry a ``source_path``
        # field for v2 evergreens (BL-058 deferred that to BL-058b),
        # so the wikilink in the rendered body is the only durable
        # back-reference we can rely on without a schema migration.
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        m = re.search(
            r"##\s*Source\s*\n+\s*-\s*\[\[([^\]]+)\]\]",
            text, flags=re.MULTILINE,
        )
        if m:
            raw_stem = m.group(1).strip()
        else:
            # Older v1 evergreens: scan body for any wikilink targeting
            # the 03-Processed area (less reliable but keeps the lineage
            # card useful for legacy notes too).
            m = re.search(r"\[\[([^\]]*?(?:_深度解读|github|article))\]\]", text)
            if m:
                raw_stem = m.group(1).strip()
        own_slug = abs_path.stem
        if own_slug:
            target_evergreen_slugs.append(own_slug)
    else:
        # Raw source — ``raw_stem`` is the file's basename without ``.md``.
        raw_stem = abs_path.stem

    sibling_evergreens: list[dict[str, str]] = []
    clusters: list[dict[str, Any]] = []
    crystals: list[dict[str, Any]] = []

    with sqlite3.connect(db_path) as conn:
        if raw_stem:
            # First-choice strategy: query the indexed ``page_links``
            # table where each row is one resolved wikilink.  Cheap
            # JOIN, scales linearly with the in-degree of the target.
            try:
                rows = conn.execute(
                    """
                    SELECT pi.slug, pi.title
                      FROM page_links pl
                      JOIN pages_index pi ON pi.slug = pl.source_slug
                     WHERE pi.note_type = 'evergreen'
                       AND (pl.target_slug = ? OR pl.target_raw = ?)
                     ORDER BY pi.slug
                     LIMIT ?
                    """,
                    (raw_stem, raw_stem, LINEAGE_SIBLING_EVERGREEN_LIMIT),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            # Fallback: ``page_links`` only stores rows whose target
            # was resolvable to a slug already in ``pages_index``.  The
            # file scanner (knowledge_index.py:1062) drops unresolved
            # wikilinks entirely — and raw intake sources in
            # ``50-Inbox/03-Processed`` are NOT scanned into
            # ``pages_index`` (only Evergreen / Atlas / 20-Areas are),
            # so the ``## Source`` link to a raw-source basename like
            # ``2026-04-28_neuphonic_neutts`` produces zero
            # ``page_links`` rows.  The body-LIKE scan below recovers
            # those cases.  Once BL-058b adds a typed ``source_stem``
            # column to ``pages_index`` (or a typed ``source_path``
            # field to evergreen frontmatter that knowledge_index
            # surfaces), this fallback can be removed.
            if not rows:
                # ``ESCAPE`` lets the LIKE pattern carry literal ``%``
                # / ``_`` / ``\`` characters in raw stems without false
                # positives.  Trigram-FTS would be faster but
                # ``page_fts`` strips brackets when tokenising so
                # phrase-matching ``[[<stem>]]`` doesn't beat LIKE.
                escaped = (
                    raw_stem.replace("\\", "\\\\")
                            .replace("%", "\\%")
                            .replace("_", "\\_")
                )
                try:
                    rows = conn.execute(
                        """
                        SELECT slug, title FROM pages_index
                         WHERE note_type = 'evergreen'
                           AND body LIKE ? ESCAPE '\\'
                         ORDER BY slug
                         LIMIT ?
                        """,
                        (f"%[[{escaped}]]%", LINEAGE_SIBLING_EVERGREEN_LIMIT),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            for slug, title in rows:
                sibling_evergreens.append({
                    "slug": str(slug),
                    "title": str(title or slug),
                    "note_href": _scoped_path(
                        f"/note?path={quote(f'10-Knowledge/Evergreen/{slug}.md', safe='')}",
                        pack_name=requested_pack,
                    ),
                })
                if slug not in target_evergreen_slugs:
                    target_evergreen_slugs.append(str(slug))

        # Forward chain: clusters that contain any of our evergreen
        # slugs.  ``member_object_ids_json`` is a JSON array of object
        # ids that match the evergreen slug.
        if target_evergreen_slugs:
            try:
                cluster_rows = conn.execute(
                    """
                    SELECT cluster_id, label, member_object_ids_json
                      FROM graph_clusters
                     WHERE cluster_kind = 'louvain_community'
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                cluster_rows = []
            slug_set = set(target_evergreen_slugs)
            for cluster_id, label, members_json in cluster_rows:
                try:
                    members = set(json.loads(members_json or "[]"))
                except json.JSONDecodeError:
                    continue
                hit = members & slug_set
                if not hit:
                    continue
                from ovp_pipeline.synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("community", str(cluster_id))
                clusters.append({
                    "cluster_id": str(cluster_id),
                    "label": str(label or "(untitled)"),
                    "member_count": len(members),
                    "matched": sorted(hit),
                    "cluster_href": _scoped_path(
                        f"/ops/cluster?id={quote(str(cluster_id), safe='')}",
                        pack_name=requested_pack,
                    ),
                    "crystal_note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })

            # Crystals — community first, contradictions second.
            try:
                crystal_rows = conn.execute(
                    """
                    SELECT cluster_id, source_evergreen_slugs_json
                      FROM community_crystals
                     WHERE superseded_by_synthesized_at = ''
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                crystal_rows = []
            for cluster_id, slugs_json in crystal_rows:
                try:
                    slugs = set(json.loads(slugs_json or "[]"))
                except json.JSONDecodeError:
                    continue
                if not (slugs & slug_set):
                    continue
                from ovp_pipeline.synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("community", str(cluster_id))
                crystals.append({
                    "kind": "community_crystal",
                    "crystal_id": str(cluster_id),
                    "label": str(cluster_id),
                    "note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })
            try:
                contra_rows = conn.execute(
                    """
                    SELECT contradiction_id, subject_key, source_object_ids_json
                      FROM contradiction_crystals
                     WHERE superseded_by_synthesized_at = ''
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                contra_rows = []
            for contradiction_id, subject_key, source_ids_json in contra_rows:
                try:
                    sources = set(json.loads(source_ids_json or "[]"))
                except json.JSONDecodeError:
                    continue
                if not (sources & slug_set):
                    continue
                from ovp_pipeline.synthesis._shared import crystal_safe_id
                safe_id = crystal_safe_id("contradiction", str(contradiction_id))
                crystals.append({
                    "kind": "contradiction_crystal",
                    "crystal_id": str(contradiction_id),
                    "label": str(subject_key or contradiction_id),
                    "note_href": _scoped_path(
                        f"/note?path=40-Resources/Crystals/{safe_id}.md",
                        pack_name=requested_pack,
                    ),
                })

    raw_source_info: dict[str, str] | None = None
    if raw_stem:
        # Locate the raw source file under 03-Processed.  The basename
        # → path mapping is unambiguous because Phase A's output filenames
        # are already disambiguated by ``<date>_<owner>_<repo>``.
        candidates = list(
            (vault_root / "50-Inbox" / "03-Processed").rglob(f"{raw_stem}.md")
        )
        if candidates:
            rel_target = candidates[0].relative_to(vault_root)
            raw_source_info = {
                "slug": raw_stem,
                "path": str(rel_target),
                "note_href": _scoped_path(
                    f"/note?path={quote(str(rel_target), safe='')}",
                    pack_name=requested_pack,
                ),
            }
        else:
            # File may have been archived already (Phase A re-process
            # moves the legacy deep-dive into 70-Archive).  Surface the
            # stem so the user can grep for it.
            raw_source_info = {
                "slug": raw_stem,
                "path": "",
                "note_href": "",
            }

    return {
        "kind": "evergreen" if is_evergreen else "raw_source",
        "raw_source": raw_source_info,
        "evergreens": sibling_evergreens,
        "clusters": sorted(clusters, key=lambda c: -int(c.get("member_count", 0))),
        "crystals": crystals,
    }



def _existing_object_rows(
    vault_dir: Path | str,
    object_ids: list[str],
    *,
    pack_name: str | None = None,
) -> dict[str, str]:
    normalized_object_ids = list(dict.fromkeys(object_id for object_id in object_ids if object_id))
    if not normalized_object_ids:
        return {}
    db_path = _db_path(vault_dir)
    placeholders = ",".join("?" for _ in normalized_object_ids)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT object_id, title
            FROM objects
            WHERE object_id IN ({placeholders})
            """,
            tuple(normalized_object_ids),
        ).fetchall()
    return {str(object_id): str(title) for object_id, title in rows}



def _fetch_activity_rows(
    conn: sqlite3.Connection,
    event_types: tuple[str, ...],
    date_key: str,
    effective_pack: str,
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Rows for an Activity card / its drilldown, scoped by
    operator-local day (BL-102) and pack.

    Day bucketing is done in Python via the shared
    ``audit_time.local_day`` so UTC-``Z`` and naive-local rows fall
    on the same operator day — SQLite ``date(timestamp)`` mixed the
    two clocks.  A coarse ``substr`` prefilter (±1 day) keeps the
    Python scan bounded; a tz shift moves a row at most one calendar
    day.  Pack scoping: matching pack included, different pack
    excluded, legacy pack-less rows only under the default pack.
    Both the card count and the drilldown call THIS, so they cannot
    disagree.
    """
    if not event_types:
        return []
    try:
        anchor = _dt.datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        return []
    day_prefixes = [
        (anchor + _dt.timedelta(days=d)).strftime("%Y-%m-%d")
        for d in (-1, 0, 1)
    ]
    et_ph = ",".join("?" for _ in event_types)
    pre_ph = ",".join("?" for _ in day_prefixes)
    raw = conn.execute(
        f"""
        SELECT timestamp, event_type, slug, payload_json
          FROM audit_events
         WHERE event_type IN ({et_ph})
           AND substr(timestamp, 1, 10) IN ({pre_ph})
        """,
        (*event_types, *day_prefixes),
    ).fetchall()
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for ts, et, slug, pj in raw:
        if _audit_local_day(str(ts or "")) != date_key:
            continue
        try:
            payload = json.loads(pj or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        row_pack = _audit_row_pack(payload)
        if row_pack is None:
            if effective_pack != PRIMARY_PACK_NAME:
                continue
        elif row_pack != effective_pack:
            continue
        out.append(
            (str(ts or ""), str(et or ""), str(slug or ""), payload)
        )
    return out



def _object_kind_profile(detail: dict[str, Any], *, relation_count: int) -> dict[str, object]:
    from ovp_pipeline.object_kinds import normalize_kind

    object_row = detail["object"]
    object_kind = normalize_kind(str(object_row.get("object_kind") or "").strip())
    spec = _OBJECT_KIND_READER_PROFILES.get(object_kind)
    if spec is None:
        return {
            "kind": object_kind or "object",
            "layout": "object_brief",
            "title": f"{_object_kind_label(object_kind)} Brief",
            "primary_question": "What is this object, what supports it, and where should I read next?",
            "reading_prompts": [
                {
                    "label": "Summary",
                    "detail": "Start with the compiled summary and claims.",
                },
                {
                    "label": "Evidence",
                    "detail": "Check source notes and evidence rows before reuse.",
                },
                {
                    "label": "Connections",
                    "detail": f"Follow {relation_count} relation(s) and backlinks for surrounding context.",
                },
            ],
            "section_labels": {},
        }
    return {
        "kind": object_kind,
        "layout": spec["layout"],
        "title": spec["title"],
        "primary_question": spec["primary_question"],
        "reading_prompts": [
            {"label": label, "detail": detail_text}
            for label, detail_text in spec["prompts"]
        ],
        "section_labels": dict(spec["section_labels"]),
    }



def _read_lifecycle_summary(
    vault_dir: Path | str,
    *,
    pack: str,
) -> dict[str, Any]:
    """Read the five-state lifecycle distribution from ``ops_state``.

    Returns ``{"available": False, "reason": ...}`` when the
    projection table doesn't exist yet (e.g. the ``ops_state`` DAG
    step hasn't run).  Returns ``{"available": True, "counts": {…}}``
    otherwise.

    Keeping this in ``view_models`` rather than calling
    ``ops_state.counts_from_projection`` directly avoids ``view_models``
    accidentally creating the table on a vault that hasn't run the
    DAG yet — we read what's there; we don't write.
    """
    from ovp_pipeline.ops_lifecycle import ALL_STATES

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {"available": False, "reason": "knowledge_index has not been built yet"}

    effective_pack = pack or PRIMARY_PACK_NAME
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='ops_state'"
            ).fetchone()
            if row is None:
                return {
                    "available": False,
                    "reason": "ops_state projection not built yet — run `ovp-ops-state --rebuild`",
                }
            rows = conn.execute(
                "SELECT state, COUNT(*) FROM ops_state "
                " WHERE pack = ? GROUP BY state",
                (effective_pack,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"available": False, "reason": f"ops_state read failed: {exc}"}

    counts: dict[str, int] = {s: 0 for s in ALL_STATES}
    for state, count in rows:
        if state in counts:
            counts[state] = int(count)
    return {
        "available": True,
        "pack": effective_pack,
        "counts": counts,
        "total": sum(counts.values()),
    }



def _search_note_type_label(note_type: str) -> str:
    normalized = (note_type or "note").replace("_", " ").strip().title()
    if not normalized:
        normalized = "Note"
    if normalized.endswith("Note") or normalized.endswith("Notes"):
        return _plural_reader_label(normalized)
    return f"{normalized} Notes"



def _source_excerpt_for_object(
    vault_dir: Path | str,
    *,
    note_path: str,
    object_id: str,
    title: str,
) -> str:
    if not note_path:
        return ""
    path = resolve_vault_dir(vault_dir) / note_path
    if not path.exists() or not path.is_file():
        return ""
    needles = [
        f"[[{object_id}]]",
        f"[[{title}]]",
        object_id,
        title,
    ]
    try:
        in_frontmatter = False
        with path.open(encoding="utf-8") as handle:
            for index, raw_line in enumerate(handle):
                raw_line = raw_line.rstrip("\n")
                if raw_line.strip() == "---":
                    if index == 0:
                        in_frontmatter = True
                        continue
                    if in_frontmatter:
                        in_frontmatter = False
                        continue
                if in_frontmatter:
                    continue
                line = _clean_excerpt_line(raw_line)
                if not line or line.startswith("---") or line.startswith("#"):
                    continue
                lowered = line.lower()
                if any(needle and needle.lower() in lowered for needle in needles):
                    return line[:240]
    except (OSError, UnicodeDecodeError):
        return ""
    return ""



def _stage_runs_for_day(
    conn: sqlite3.Connection, date_key: str, effective_pack: str
) -> dict[str, dict[str, Any]]:
    """Roll the BL-103b ``stage_*`` audit events up to the latest run
    per stage on ``date_key`` (operator-local, pack-scoped).

    Derived on read from ``audit_events`` rather than a materialized
    ``ops_stage_runs`` table: the rollup is a pure projection of the
    audit ledger (rebuildable by definition) and avoids a
    knowledge.db schema migration + version-gate risk for a
    read-only surface.  A physical table can follow if a writer ever
    needs it.
    """
    et_ph = ",".join("?" for _ in _STAGE_EVENT_TYPES)
    rows = conn.execute(
        f"""
        SELECT timestamp, event_type, payload_json
          FROM audit_events
         WHERE event_type IN ({et_ph})
        """,
        _STAGE_EVENT_TYPES,
    ).fetchall()
    # (stage, run_id) → {events: {event_type: payload}, ts: latest}
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    for ts, et, pj in rows:
        if _audit_local_day(str(ts or "")) != date_key:
            continue
        try:
            payload = json.loads(pj or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        rp = _audit_row_pack(payload)
        if rp is None:
            if effective_pack != PRIMARY_PACK_NAME:
                continue
        elif rp != effective_pack:
            continue
        stage = str(payload.get("stage") or "")
        if not stage:
            continue
        run_id = str(payload.get("run_id") or "")
        key = (stage, run_id)
        slot = runs.setdefault(key, {"events": {}, "ts": ""})
        slot["events"][str(et)] = payload
        tsx = str(ts or "")
        if tsx > slot["ts"]:
            slot["ts"] = tsx
    # Collapse to the latest run per stage.
    latest: dict[str, dict[str, Any]] = {}
    for (stage, _rid), slot in runs.items():
        cur = latest.get(stage)
        if cur is None or slot["ts"] > cur["_ts"]:
            ev = slot["events"]
            if "stage_failed" in ev:
                status = "failed"
                term = ev["stage_failed"]
            elif "stage_completed" in ev:
                status = "completed"
                term = ev["stage_completed"]
            elif "stage_skipped" in ev:
                status = "skipped"
                term = ev["stage_skipped"]
            else:
                status = "started"
                term = ev.get("stage_started", {})
            latest[stage] = {
                "status": status,
                "input": term.get("input_count"),
                "output": term.get("output_count"),
                "_ts": slot["ts"],
            }
    return latest



def _state_for_event_types(event_types: tuple[str, ...]) -> str:
    """Infer the lifecycle state a card-drilldown belongs to from its
    event_types set.  Card links carry exactly a card's composed
    event_types, so an exact set match resolves the state without an
    extra URL param.  Empty string when it can't be resolved (the
    drilldown then shows rows but no distinct-item reconciliation)."""
    want = set(event_types)
    if not want:
        return ""
    for card_def in M25_LIFECYCLE_CARD_DEFS:
        if set(_event_types_for_card(card_def)) == want:
            return str(card_def["id"])
    return ""



def build_action_queue_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = list_action_queue(vault_dir, pack_name=pack_name, status=status, query=query)
    return {
        "screen": "actions/browser",
        "requested_pack": requested_pack,
        "governance_contract": describe_governance_contract(pack_name=pack_name),
        "items": items,
        "count": len(items),
        "query": query or "",
        "status": status or "",
        "status_counts": dict(Counter(str(item["status"]) for item in items)),
        "impact_counts": _impact_counts(items),
        "queued_safe_count": sum(1 for item in items if item.get("status") == "queued" and item.get("safe_to_run")),
        "failed_count": sum(1 for item in items if item.get("status") == "failed"),
        "failure_buckets": dict(
            Counter(
                str(item.get("failure_bucket") or "")
                for item in items
                if item.get("status") == "failed" and str(item.get("failure_bucket") or "")
            )
        ),
    }



def build_candidate_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_CANDIDATE_BROWSER_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    payload = list_candidate_concepts(vault_dir, query=query, limit=limit, offset=offset)
    payload["requested_pack"] = requested_pack
    payload["operator_rail"] = [
        _operator_action(
            "Orientation Brief",
            _scoped_path("/ops/briefing", pack_name=requested_pack),
            "Read the compiled context before changing canonical concepts.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Check whether this candidate is attached to active production signals.",
        ),
        _operator_action(
            "Actions",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Inspect queued work that may depend on candidate canonicalization.",
        ),
        _operator_action(
            "Objects",
            _scoped_path("/ops/objects", pack_name=requested_pack),
            "Compare candidates against active Evergreen objects.",
        ),
    ]
    payload["query"] = query or ""
    return payload



def build_contradiction_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    status: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    raw_items = list_contradictions(vault_dir, pack_name=pack_name, status=status, query=query)
    provenance_map = get_object_provenance_map(
        vault_dir,
        _object_ids_from_claim_ids(
            *(
                item["positive_claim_ids"] + item["negative_claim_ids"]
                for item in raw_items
            )
        ),
        pack_name=pack_name,
    )
    items = []
    for item in raw_items:
        object_ids = _object_ids_from_claim_ids(item["positive_claim_ids"], item["negative_claim_ids"])
        source_notes: dict[str, dict[str, Any]] = {}
        mocs: dict[str, dict[str, Any]] = {}
        object_titles: dict[str, str] = {}
        for object_id in object_ids:
            provenance = provenance_map.get(
                object_id,
                {"title": object_id, "evergreen_path": "", "source_notes": [], "mocs": []},
            )
            object_titles[object_id] = provenance["title"]
            for note in provenance["source_notes"]:
                source_notes.setdefault(note["slug"], note)
            for moc in provenance["mocs"]:
                mocs.setdefault(moc["slug"], moc)
        items.append(
            {
                **item,
                "object_ids": object_ids,
                "object_titles": object_titles,
                "object_links": [
                    {
                        "object_id": object_id,
                        "path": _scoped_path(
                            f"/object?id={quote(object_id, safe='')}",
                            pack_name=requested_pack,
                        ),
                    }
                    for object_id in object_ids
                ],
                "provenance": {
                    "source_notes": list(source_notes.values()),
                    "mocs": list(mocs.values()),
                },
            }
    )
    status_counts = Counter(item["status"] for item in items)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(items)} contradiction rows are currently visible, with {status_counts.get('open', 0)} still open.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status"],
                    }
                    for item in items[:4]
                ]
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{len({object_id for item in items for object_id in item['object_ids']})} objects and {len({note['slug'] for item in items for note in item['provenance']['source_notes']})} source notes are affected by the visible contradiction scope.",
            items=[
                {"kind": "filter", "label": status or "all", "path": "", "detail": "Current contradiction filter."},
                {"kind": "query", "label": query or "all", "path": "", "detail": "Current query scope."},
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary="Contradictions are anchored by ranked evidence and provenance across source notes and atlas pages.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": note["title"],
                        "path": _scoped_path(f"/note?path={quote(note['path'], safe='')}", pack_name=requested_pack),
                        "detail": note["note_type"],
                    }
                    for item in items[:3]
                    for note in item["provenance"]["source_notes"][:1]
                ]
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{status_counts.get('open', 0)} open rows still require review or dismissal.",
            items=[
                *[
                    {
                        "kind": "open_contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status_explanation"],
                    }
                    for item in items[:4]
                    if item["status"] == "open"
                ]
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Route from contradiction review into object pages and downstream maintenance.",
            items=[
                *[
                    {
                        "kind": "object",
                        "label": item["object_titles"].get(link["object_id"], link["object_id"]),
                        "path": link["path"],
                        "detail": "Open affected object page.",
                    }
                    for item in items[:2]
                    for link in item["object_links"][:2]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open active signals for related maintenance entry points.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Inspect queued or failed execution work.",
        ),
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Trace production gaps behind the visible contradictions.",
        ),
        _operator_action(
            "Events",
            _scoped_path("/ops/events", pack_name=requested_pack),
            "Compare contradiction scope against the timeline surface.",
        ),
    ]
    return {
        "screen": "truth/contradictions",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("contradiction_view", pack_name=pack_name),
        "items": items,
        "count": len(items),
        "open_count": status_counts.get("open", 0),
        "resolved_count": sum(count for status, count in status_counts.items() if status != "open"),
        "scope_summary": {
            "item_count": len(items),
            "object_count": len({object_id for item in items for object_id in item["object_ids"]}),
            "source_note_count": len(
                {
                    note["slug"]
                    for item in items
                    for note in item["provenance"]["source_notes"]
                }
            ),
        },
        "detection_contract": {
            "model": "page_summary_polarity",
            "confidence": "heuristic",
            "polarity_semantics": "Positive and negative claim sets are compared within the same contradiction subject scope.",
            "evidence_semantics": "Ranked evidence is assembled from claim_evidence rows attached to both polarity sides.",
            "status_buckets": {
                "open": status_counts.get("open", 0),
                "reviewed": sum(count for row_status, count in status_counts.items() if row_status != "open"),
            },
            "status_explanations": CONTRADICTION_STATUS_EXPLANATIONS,
        },
        "detection_notes": [
            "Contradictions are currently detected from page_summary claim polarity, not from full semantic contradiction analysis.",
            "Zero results do not prove consistency; they usually mean the current heuristic did not detect a conflict.",
            CONTRADICTION_HEURISTIC_NOTE,
        ],
        "empty_state": "Zero results usually means the current heuristic did not detect a conflict, not that the vault is globally contradiction-free.",
        "operator_rail": operator_rail,
        "status": status or "",
        "query": query or "",
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }



def build_event_dossier_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    event_types_filter: tuple[str, ...] = (),
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    research_shell_enabled = _supports_research_shell(pack_name)
    effective_limit = DEFAULT_EVENT_DOSSIER_LIMIT if limit is None else limit
    normalized_from = (from_date or "").strip() or None
    normalized_to = (to_date or "").strip() or None
    # M24.0 stop-gap: when an event_types filter is set, over-fetch
    # so post-filter trim still has matches.  Filtering after the
    # pre-limited fetch (CodeRabbit Major) would drop legitimately
    # matching rows that happened to sit past the first
    # ``effective_limit`` rows in the timeline.
    query_limit = effective_limit or DEFAULT_EVENT_DOSSIER_LIMIT
    if event_types_filter:
        query_limit = max(query_limit, 1000)
    events = [
        _build_timeline_event_item(row)
        for row in list_timeline_events(
            vault_dir,
            pack_name=pack_name,
            query=query,
            limit=query_limit,
            from_date=normalized_from,
            to_date=normalized_to,
        )
    ]
    if event_types_filter:
        allowed = frozenset(event_types_filter)
        events = [e for e in events if e.get("event_type") in allowed]
        # Trim back to caller's effective_limit after filtering.
        if effective_limit and effective_limit > 0:
            events = events[:effective_limit]
    provenance_map = get_object_provenance_map(
        vault_dir,
        [event["object_id"] for event in events],
        pack_name=pack_name,
    )
    scoped_object_ids = [event["object_id"] for event in events]
    review_context = get_review_context(vault_dir, scoped_object_ids, pack_name=pack_name)
    scoped_stale_summaries = list_stale_summaries(
        vault_dir,
        pack_name=pack_name,
        object_ids=scoped_object_ids,
        limit=100,
    )
    scoped_contradictions = [
        item
        for item in list_contradictions(vault_dir, pack_name=pack_name, limit=200)
        if any(claim_id.split("::", 1)[0] in set(scoped_object_ids) for claim_id in item["positive_claim_ids"] + item["negative_claim_ids"])
        and item["status"] == "open"
    ]
    for event in events:
        event["object_path"] = _scoped_path(
            f"/object?id={quote(str(event['object_id']), safe='')}",
            pack_name=requested_pack,
        )
        event["review_links"] = {
            "object_path": event["object_path"],
            "topic_path": _scoped_path(
                f"/topic?id={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "contradictions_path": _scoped_path(
                f"/ops/contradictions?q={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
            "summaries_path": _scoped_path(
                f"/ops/summaries?q={quote(str(event['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        event["provenance"] = provenance_map.get(
            event["object_id"],
            {"evergreen_path": "", "source_notes": [], "mocs": []},
        )
    dates = sorted({event["event_date"] for event in events}, reverse=True)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["event_date"], []).append(event)
    cluster_sections = [
        {
            "date": date,
            "clusters": _cluster_timeline_events(grouped[date]),
        }
        for date in dates
    ]
    event_type_counts = Counter(event["event_kind"] for event in events)
    row_type_counts = Counter(event["row_type"] for event in events)
    anchor_kind_counts = Counter(event["timeline_anchor_kind"] for event in events)
    semantic_roles = Counter(event["semantic_role"] for event in events)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=f"{len(events)} timeline rows grouped into {sum(len(section['clusters']) for section in cluster_sections)} visible event clusters.",
            items=[
                *[
                    {
                        "kind": "event_cluster",
                        "label": item["title"],
                        "path": item["review_links"]["topic_path"],
                        "detail": f"{item['row_count']} timeline rows",
                    }
                    for section in cluster_sections[:2]
                    for item in section["clusters"][:2]
                ]
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=f"{review_context.get('open_contradiction_count', 0)} contradictions and {review_context.get('stale_summary_count', 0)} stale summaries appear in the visible event scope.",
            items=[
                {"kind": "query", "label": query or "All events", "path": "", "detail": "Current dossier filter scope."},
                {
                    "kind": "contradictions",
                    "label": "Contradiction review",
                    "path": _scoped_path(f"/ops/contradictions?q={quote(query or '', safe='')}", pack_name=requested_pack) if query else _scoped_path("/ops/contradictions", pack_name=requested_pack),
                    "detail": "Inspect tensions in the visible event scope.",
                },
            ],
        ),
        _compiled_section(
            "evidence_traceability",
            "Evidence Traceability",
            summary=f"{len({note['slug'] for event in events for note in event['provenance']['source_notes']})} source notes and {len({moc['slug'] for event in events for moc in event['provenance']['mocs']})} atlas pages anchor the visible event scope.",
            items=[
                *[
                    {
                        "kind": "source_note",
                        "label": note["title"],
                        "path": _scoped_path(f"/note?path={quote(note['path'], safe='')}", pack_name=requested_pack),
                        "detail": note["note_type"],
                    }
                    for event in events[:3]
                    for note in event["provenance"]["source_notes"][:1]
                ]
            ],
        ),
        _compiled_section(
            "open_tensions",
            "Open Tensions",
            summary=f"{len(scoped_contradictions)} contradictions and {len(scoped_stale_summaries)} stale summaries remain visible in this dossier.",
            items=[
                *[
                    {
                        "kind": "contradiction",
                        "label": item["subject_key"],
                        "path": _scoped_path(f"/ops/contradictions?q={quote(item['subject_key'], safe='')}", pack_name=requested_pack),
                        "detail": item["status"],
                    }
                    for item in scoped_contradictions[:3]
                ],
                *[
                    {
                        "kind": "stale_summary",
                        "label": item["title"],
                        "path": item["object_path"],
                        "detail": ", ".join(item["reason_texts"]),
                    }
                    for item in scoped_stale_summaries[:2]
                ],
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Continue from the timeline into object, contradiction, and summary review surfaces.",
            items=[
                *[
                    {
                        "kind": "topic",
                        "label": item["title"],
                        "path": item["review_links"]["topic_path"],
                        "detail": "Open topic context for this event cluster.",
                    }
                    for item in events[:3]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Production Browser",
            _scoped_path("/ops/production", pack_name=requested_pack),
            "Inspect production chains behind the visible timeline scope.",
        ),
        _operator_action(
            "Contradictions",
            _scoped_path(
                f"/ops/contradictions?q={quote(query or '', safe='')}",
                pack_name=requested_pack,
            )
            if query
            else _scoped_path("/ops/contradictions", pack_name=requested_pack),
            "Review contradiction rows for the current dossier scope.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Open the active signal queue.",
        ),
        _operator_action(
            "Clusters" if research_shell_enabled else "Search",
            _scoped_path("/ops/clusters" if research_shell_enabled else "/search", pack_name=requested_pack),
            (
                "Explore graph clusters connected to current work."
                if research_shell_enabled
                else "Search laterally from the current shell."
            ),
        ),
    ]
    return {
        "screen": "event/dossier",
        "requested_pack": requested_pack,
        "assembly_contract": _assembly_contract("event_dossier", pack_name=pack_name),
        "events": events,
        "event_count": len(events),
        "cluster_count": sum(len(section["clusters"]) for section in cluster_sections),
        "dates": dates,
        "cluster_sections": cluster_sections,
        "event_type_counts": dict(event_type_counts),
        # M24.0 stop-gap: surface the filter so the renderer can warn
        # when an incoming ``event_types=`` filter returns 0 rows
        # because this page is a *timeline projection*, not a raw
        # audit-event browser.  The ``/ops/today`` cards count raw
        # audit_events; the timeline only contains dated note /
        # heading / contradiction projections.  Without the warning,
        # an operator clicks "See all 27 →" and sees 0 rows and
        # thinks the data is wrong — actually the data sources just
        # differ.  M25's ``/ops/items`` unifies them.
        "event_types_filter": list(event_types_filter),
        "limit": effective_limit,
        "is_limited": effective_limit is not None,
        "from_date": normalized_from or "",
        "to_date": normalized_to or "",
        "timeline_contract": {
            "timeline_kind": "dated_note_projection",
            "grouping_kind": "object_date_rollup",
            "row_type_counts": dict(row_type_counts),
            "anchor_kind_counts": dict(anchor_kind_counts),
            "semantic_roles": dict(semantic_roles),
            "event_vs_note_explanation": (
                "Event Dossier groups dated note and heading projections by object and date; "
                "it is not a canonical event entity store."
            ),
        },
        "production_summary": _build_production_summary(
            vault_dir,
            scoped_object_ids,
            pack_name=pack_name,
        ),
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=scoped_object_ids, limit=8),
        "scoped_object_ids": list(dict.fromkeys(scoped_object_ids)),
        "scoped_stale_summary_ids": [item["object_id"] for item in scoped_stale_summaries],
        "scoped_open_contradiction_ids": [item["contradiction_id"] for item in scoped_contradictions],
        "model_notes": [
            "Event Dossier is a timeline over dated notes projected from indexed pages, not a separate event entity system.",
            "page_date rows come from note-level dates; heading_date rows come from dated section headings.",
        ],
        "operator_rail": operator_rail,
        "query": query or "",
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }



def build_intake_cohort_payload(
    vault_dir: Path | str, *, date_key: str, pack: str
) -> dict[str, Any]:
    """BL-105: "Flow by intake day".  For the sources whose FIRST
    durable intake (operator-local day) is ``date_key``, show where
    they are *now* — current ``ops_state`` distribution, age, and
    how many stalled in Received/Extracted.

    Answers the operator's actual question — "what happened to the
    articles I saved that day?" — which Activity (event-day) cannot:
    a source saved on the 10th but absorbed on the 16th shows up in
    the 10th's cohort here, but in the 16th's Activity there.

    Identity + pack scoping reuse the BL-101 helpers; needs only
    intake-class audit evidence + current ``ops_state`` (both exist
    post-PR-B), so it ships before the BL-103b stage ledger.
    """
    from ovp_pipeline.ops_lifecycle import ALL_STATES

    db_path = _db_path(vault_dir)
    base: dict[str, Any] = {
        "screen": "ops/intake-cohort",
        "date": date_key,
        "requested_pack": pack,
        "available": False,
        "reason": "",
        "cohort_size": 0,
        "distribution": {s: 0 for s in ALL_STATES},
        "untracked": 0,
        "stalled": 0,
        "stall_days": _INTAKE_STALL_DAYS,
        "oldest_age_days": 0,
        "samples": [],
    }
    if not db_path.exists():
        base["reason"] = "knowledge_index has not been built yet"
        return base

    intake_types = tuple(_evt_for_category("intake"))
    if not intake_types:
        base["available"] = True
        return base
    effective_pack = pack or PRIMARY_PACK_NAME

    # Earliest intake instant per source identity, across ALL
    # history — we can only know a source's cohort day by proving it
    # had no earlier intake.  (Full intake-subset scan; BL-108
    # streaming is the perf follow-up, not a blocker here.)
    earliest: dict[str, Any] = {}
    state_by_id: dict[str, str] = {}
    with sqlite3.connect(db_path) as conn:
        et_ph = ",".join("?" for _ in intake_types)
        for ts, slug, pj in conn.execute(
            f"SELECT timestamp, slug, payload_json FROM audit_events "
            f" WHERE event_type IN ({et_ph})",
            intake_types,
        ):
            parsed = _parse_audit_ts(str(ts or ""))
            if parsed is None:
                continue
            try:
                payload = json.loads(pj or "{}")
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            rp = _audit_row_pack(payload)
            if rp is None:
                if effective_pack != PRIMARY_PACK_NAME:
                    continue
            elif rp != effective_pack:
                continue
            ident = _source_identity(str(slug or ""), payload)
            if not ident:
                continue
            cur = earliest.get(ident)
            if cur is None or parsed < cur:
                earliest[ident] = parsed

        cohort = {
            sid: ts
            for sid, ts in earliest.items()
            if ts.astimezone().date().isoformat() == date_key
        }
        has_ops = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='ops_state'"
        ).fetchone()
        if has_ops and cohort:
            ids = list(cohort)
            for i in range(0, len(ids), 400):
                chunk = ids[i : i + 400]
                ph = ",".join("?" for _ in chunk)
                for iid, st in conn.execute(
                    f"SELECT item_id, state FROM ops_state "
                    f" WHERE pack = ? AND item_kind = 'source' "
                    f"   AND item_id IN ({ph})",
                    (effective_pack, *chunk),
                ):
                    state_by_id[str(iid)] = str(st)

    now = _dt.datetime.now(_dt.timezone.utc)
    dist = {s: 0 for s in ALL_STATES}
    untracked = 0
    stalled = 0
    ages: list[int] = []
    samples: list[dict[str, Any]] = []
    for sid, ts in sorted(cohort.items(), key=lambda kv: kv[1]):
        st = state_by_id.get(sid)
        age = (now - ts).days
        ages.append(age)
        if st in dist:
            dist[st] += 1
        else:
            untracked += 1
        if st in ("Received", "Extracted") and age > _INTAKE_STALL_DAYS:
            stalled += 1
        if len(samples) < TODAY_CARD_SAMPLE_SIZE:
            samples.append({
                "slug": sid,
                "state": st or "Untracked",
                "intake_at": ts.astimezone().isoformat(),
                "age_days": age,
                "href": _scoped_path(
                    f"/ops/items?state={quote(st or '', safe='')}",
                    pack_name=pack,
                ),
            })

    base.update(
        available=True,
        cohort_size=len(cohort),
        distribution=dist,
        untracked=untracked,
        stalled=stalled,
        oldest_age_days=max(ages) if ages else 0,
        samples=samples,
    )
    return base



def build_objects_index_payload(
    vault_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    object_kind: str | None = None,
    pack_name: str | None = None,
    sort: str = "alpha",
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    if sort not in _OBJECTS_INDEX_VALID_SORTS:
        sort = "alpha"
    items = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in list_objects(
            vault_dir,
            limit=limit,
            offset=offset,
            query=query,
            object_kind=object_kind,
            pack_name=pack_name,
            sort=sort,
        )
    ]
    total_count = count_objects(vault_dir, query=query, object_kind=object_kind, pack_name=pack_name)

    kind_stats: list[dict[str, Any]] = []
    try:
        kind_stats = list_object_kind_stats(vault_dir, pack_name=pack_name)
    except Exception:
        pass

    return {
        "screen": "objects/index",
        "requested_pack": requested_pack,
        "projection_label": _access_projection_label(
            surface="objects_index",
            pack_name=pack_name,
            generated_by="build_objects_index_payload",
        ),
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "kind_stats": kind_stats,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "query": query or "",
        "object_kind": object_kind or "",
    }



def build_production_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="production_chains",
    )
    if surface_contract["status"] == "missing":
        return {
            "screen": "production/browser",
            "requested_pack": requested_pack,
            "surface_contract": surface_contract,
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'production_chains' surface."
            ),
            "items": [],
            "source_items": [],
            "weak_points": [],
            "count": 0,
            "query": query or "",
            "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
            "is_limited": True,
            "counts": {
                "source_notes": 0,
            },
            "operator_rail": [],
            "compiled_sections": [],
            "section_nav": [],
        }
    items = list_production_chains(
        vault_dir,
        pack_name=pack_name,
        query=query,
        limit=DEFAULT_TRACEABILITY_BROWSER_LIMIT,
    )
    source_items = [item for item in items if item["stage_label"] == "source_note"]
    weak_points = _build_production_weak_points(vault_dir, pack_name=pack_name, query=query)
    compiled_sections = [
        _compiled_section(
            "current_state",
            "Current State",
            summary=(
                f"{len(items)} production-chain entries are currently visible, spanning "
                f"{len(source_items)} source notes."
            ),
            items=[
                {
                    "kind": "source_notes",
                    "label": "Source notes",
                    "path": "",
                    "detail": f"{len(source_items)} source-note chain entries in scope.",
                },
            ],
        ),
        _compiled_section(
            "why_it_matters",
            "Why It Matters",
            summary=(
                f"{len(weak_points)} chain weak points currently block full source-to-object-to-atlas legibility."
            ),
            items=[
                *[
                    {
                        "kind": "weak_point",
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['note_path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": f"Missing {', '.join(item['missing'])}",
                    }
                    for item in weak_points[:3]
                ]
            ],
        ),
        _compiled_section(
            "chain_gaps",
            "Chain Gaps",
            summary="Weak points highlight where the current production chain stops short of a complete downstream path.",
            items=[
                *[
                    {
                        "kind": item["stage_label"],
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['note_path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": ", ".join(item["missing"]),
                    }
                    for item in weak_points[:5]
                ]
            ],
        ),
        _compiled_section(
            "where_to_go_next",
            "Where To Go Next",
            summary="Use the visible source and deep-dive entries to continue into note, object, and atlas-level traceability.",
            items=[
                *[
                    {
                        "kind": item["stage_label"],
                        "label": item["title"],
                        "path": _scoped_path(
                            f"/note?path={quote(str(item['path']), safe='')}",
                            pack_name=requested_pack,
                        ),
                        "detail": str(item["traceability"].get("chain_summary") or ""),
                    }
                    for item in items[:4]
                ]
            ],
        ),
    ]
    operator_rail = [
        _operator_action(
            "Orientation Brief",
            _scoped_path("/ops/briefing", pack_name=requested_pack),
            "Return to the current entry product.",
        ),
        _operator_action(
            "Signals",
            _scoped_path("/ops/signals", pack_name=requested_pack),
            "Review active signals related to chain maintenance.",
        ),
        _operator_action(
            "Action Queue",
            _scoped_path("/ops/actions", pack_name=requested_pack),
            "Run or inspect queued execution work.",
        ),
        _operator_action(
            "Search",
            _scoped_path("/search", pack_name=requested_pack),
            "Search laterally from the current production scope.",
        ),
    ]
    return {
        "screen": "production/browser",
        "requested_pack": requested_pack,
        "surface_contract": surface_contract,
        "items": items,
        "source_items": source_items,
        "weak_points": weak_points,
        "count": len(items),
        "query": query or "",
        "limit": DEFAULT_TRACEABILITY_BROWSER_LIMIT,
        "is_limited": True,
        "counts": {
            "source_notes": len(source_items),
        },
        "operator_rail": operator_rail,
        "compiled_sections": compiled_sections,
        "section_nav": _section_nav_from_compiled_sections(compiled_sections),
    }



def build_queue_overview_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """Counts + oldest-row hints across the four maintainer queues.

    The four queues — concept candidates, contradictions, signals
    waiting for action, action-worker tasks — historically lived on
    four different ``/ops/*`` pages with no top-level summary, so
    the operator could not tell whether the day's triage was done
    without visiting each page.  This payload powers ``/ops/queue``,
    a single landing page that answers "is there anything to do?"
    in one screen.

    The implementation is intentionally cheap: it reuses each
    queue's existing ``list_*`` function with a small ``limit`` to
    sample the oldest pending item, then counts items in
    interpretable buckets.  Healthy state (productive signals,
    completed actions, evergreens already in the truth store) is
    surfaced separately so the page makes the "no action needed"
    case visible too.
    """
    requested_pack = pack_name or ""

    # Candidates: registry load is the cost; capping at 1 is enough
    # for the oldest-pending hint — the registry's own ``count``
    # field carries the total without iterating the list.
    #
    # The candidate registry is intentionally vault-global, not pack-
    # scoped — ``ConceptRegistry`` lives at one path per vault and
    # is keyed by slug across the whole vault.  ``list_candidate_concepts``
    # therefore takes no ``pack_name`` argument; passing one would
    # raise ``TypeError``.  The other queues' pack scoping still
    # holds because contradictions / actions / signals all live in
    # per-pack tables or ledgers.
    candidate_payload = list_candidate_concepts(vault_dir, limit=1)
    candidates_first = (candidate_payload.get("candidates") or [None])[0]
    candidates_pending = int(candidate_payload.get("count") or 0)
    candidates_oldest = candidates_first

    # Contradictions: lightweight ``GROUP BY status`` + LIMIT 1
    # probe instead of fetching up to 500 rows just to count.
    contradiction_overview = count_contradictions_by_status(
        vault_dir, pack_name=pack_name
    )
    contradictions_pending = int(
        contradiction_overview.get("by_status", {}).get("open") or 0
    )
    contradictions_oldest = contradiction_overview.get("oldest_open")

    # Signals come from a JSONL ledger so we still scan once, but
    # bound the cost: read just enough to find the oldest waiting
    # row, and use the same pass for the productive count.  500
    # rows already covers any realistic active signals window.
    signals = list_signals(vault_dir, pack_name=pack_name, limit=500)
    signals_waiting = [s for s in signals if s.get("capture_status") == "waiting"]
    signals_productive = [s for s in signals if s.get("capture_status") == "productive"]
    signals_pending = len(signals_waiting)
    signals_oldest = signals_waiting[0] if signals_waiting else None

    # Action queue: lightweight pass that skips the per-row
    # resolver-metadata + contract-metadata enrichment that
    # ``list_action_queue`` runs on every row.
    action_overview = count_action_queue_by_status(vault_dir, pack_name=pack_name)
    action_by_status = action_overview.get("by_status", {})
    actions_pending = int(
        (action_by_status.get("failed") or 0) + (action_by_status.get("blocked") or 0)
    )
    actions_succeeded_count = int(action_by_status.get("succeeded") or 0)
    actions_oldest = action_overview.get("oldest_failed")

    # Evergreen/object total — informational, surfaces "you have a
    # vault" so the healthy-state line carries weight.
    try:
        evergreen_total = count_objects(vault_dir, pack_name=pack_name)
    except Exception:
        evergreen_total = 0

    queues = [
        {
            "id": "concepts",
            "label": "concept candidate" + ("s" if candidates_pending != 1 else ""),
            "count": candidates_pending,
            "browse_path": _scoped_path(
                "/ops/queue/concepts", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(candidates_oldest.get("title") or candidates_oldest.get("slug") or "")
                if candidates_oldest
                else ""
            ),
            "oldest_at": (
                str(candidates_oldest.get("last_seen_at") or "")
                if candidates_oldest
                else ""
            ),
        },
        {
            "id": "contradictions",
            "label": "contradiction" + ("s" if contradictions_pending != 1 else "") + " open",
            "count": contradictions_pending,
            "browse_path": _scoped_path(
                "/ops/queue/contradictions", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(contradictions_oldest.get("subject_key") or "")
                if contradictions_oldest
                else ""
            ),
            "oldest_at": "",
        },
        {
            "id": "signals",
            "label": "signal" + ("s" if signals_pending != 1 else "") + " waiting",
            "count": signals_pending,
            "browse_path": _scoped_path(
                "/ops/queue/signals?status=waiting", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(signals_oldest.get("title") or signals_oldest.get("signal_type") or "")
                if signals_oldest
                else ""
            ),
            "oldest_at": (
                str(signals_oldest.get("detected_at") or "")
                if signals_oldest
                else ""
            ),
        },
        {
            "id": "actions",
            "label": "action" + ("s" if actions_pending != 1 else "") + " failed/blocked",
            "count": actions_pending,
            "browse_path": _scoped_path(
                "/ops/queue/actions?status=failed", pack_name=requested_pack
            ),
            "oldest_subject": (
                str(actions_oldest.get("title") or actions_oldest.get("action_id") or "")
                if actions_oldest
                else ""
            ),
            "oldest_at": (
                str(actions_oldest.get("created_at") or "")
                if actions_oldest
                else ""
            ),
        },
    ]

    healthy = {
        "productive_signals": len(signals_productive),
        "succeeded_actions": actions_succeeded_count,
        "evergreen_total": evergreen_total,
    }

    return {
        "screen": "ops/queue",
        "requested_pack": requested_pack,
        "queues": queues,
        "pending_total": sum(q["count"] for q in queues),
        "healthy": healthy,
    }



def build_runs_index_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Index of recent transactions for ``/ops/runs``.

    Lists ``transaction_started`` rows in reverse-chronological order
    with status (completed / failed / running), workflow type, start
    + end timestamps, and event count.  Status comes from a
    ``transaction_completed`` event for the same ``txn_id`` if any —
    rows with no matching completion are flagged as ``running`` (or
    ``stale`` if older than 6 hours and still no completion).
    """
    from datetime import datetime, timedelta, timezone
    requested_pack = pack_name or ""
    cap = max(1, limit if limit is not None else DEFAULT_RUNS_INDEX_LIMIT)

    db_path = _db_path(vault_dir)
    if not db_path.exists():
        return {
            "screen": "ops/runs",
            "requested_pack": requested_pack,
            "runs": [],
            "available": False,
            "reason": "knowledge_index has not been built yet",
        }

    runs: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        # ``transaction_started`` carries ``txn_id`` and the workflow
        # ``type`` in its payload.  We pair each with an optional
        # matching ``transaction_completed`` row by ``txn_id``.
        started_rows = conn.execute(
            """
            SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                   json_extract(payload_json, '$.type')   AS workflow_type,
                   timestamp
              FROM audit_events
             WHERE event_type = 'transaction_started'
             ORDER BY timestamp DESC
             LIMIT ?
            """,
            (cap,),
        ).fetchall()

        if not started_rows:
            return {
                "screen": "ops/runs",
                "requested_pack": requested_pack,
                "runs": [],
                "available": True,
            }

        txn_ids = tuple(row[0] for row in started_rows if row[0])
        completed_lookup: dict[str, str] = {}
        if txn_ids:
            placeholders = ",".join("?" for _ in txn_ids)
            for tid, ts in conn.execute(
                f"""
                SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                       timestamp
                  FROM audit_events
                 WHERE event_type = 'transaction_completed'
                   AND json_extract(payload_json, '$.txn_id') IN ({placeholders})
                """,
                txn_ids,
            ).fetchall():
                if tid:
                    completed_lookup[str(tid)] = str(ts)

            # Per-txn event counts so the index page shows magnitude.
            count_lookup: dict[str, int] = {}
            for tid, n in conn.execute(
                f"""
                SELECT json_extract(payload_json, '$.txn_id') AS txn_id,
                       COUNT(*) AS n
                  FROM audit_events
                 WHERE json_extract(payload_json, '$.txn_id') IN ({placeholders})
                 GROUP BY txn_id
                """,
                txn_ids,
            ).fetchall():
                if tid:
                    count_lookup[str(tid)] = int(n)
        else:
            count_lookup = {}

    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=RUNS_STALE_AFTER_HOURS)
    for txn_id, workflow_type, started_at in started_rows:
        if not txn_id:
            continue
        completed_at = completed_lookup.get(str(txn_id), "")
        if completed_at:
            status = "completed"
        else:
            try:
                started_dt = datetime.fromisoformat(
                    str(started_at).replace("Z", "+00:00")
                )
                # PipelineLogger writes naive UTC timestamps for some
                # events (no trailing Z, no offset).  Treat them as
                # UTC so the < comparison doesn't crash.
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                status = "stale" if started_dt < stale_cutoff else "running"
            except ValueError:
                status = "running"
        runs.append({
            "txn_id": str(txn_id),
            "workflow_type": str(workflow_type or "(unknown)"),
            "started_at": str(started_at or ""),
            "completed_at": completed_at,
            "status": status,
            "event_count": count_lookup.get(str(txn_id), 0),
            "detail_href": _scoped_path(
                f"/ops/runs/{quote(str(txn_id), safe='')}",
                pack_name=requested_pack,
            ),
        })

    # Day grouping — build ``[(day, [run, ...])]`` so the renderer can
    # emit one section per calendar day in chronological order, with
    # explicit ``Idle`` markers for days that contained no runs.  The
    # operator's mental model is "what did the pipeline do this week";
    # day-grouped output makes weekend gaps and broken-cron days
    # immediately obvious.
    from datetime import timedelta as _timedelta
    runs_by_day: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        ts = str(run.get("started_at", ""))[:10]
        if not ts:
            continue
        runs_by_day.setdefault(ts, []).append(run)

    day_groups: list[dict[str, Any]] = []
    if runs_by_day:
        sorted_days = sorted(runs_by_day.keys(), reverse=True)
        try:
            newest = datetime.strptime(sorted_days[0], "%Y-%m-%d").date()
            oldest = datetime.strptime(sorted_days[-1], "%Y-%m-%d").date()
        except ValueError:
            newest = oldest = None
        if newest and oldest:
            cur = newest
            while cur >= oldest:
                key = cur.strftime("%Y-%m-%d")
                day_runs = runs_by_day.get(key, [])
                day_groups.append({
                    "date": key,
                    "runs": day_runs,
                    "count": len(day_runs),
                    "idle": not day_runs,
                })
                cur -= _timedelta(days=1)
        else:
            for key in sorted_days:
                day_groups.append({
                    "date": key,
                    "runs": runs_by_day[key],
                    "count": len(runs_by_day[key]),
                    "idle": False,
                })

    # Window summary — surface the implicit time range the limit
    # imposes so the operator knows whether the page reflects "today"
    # or "the last fortnight".
    if runs:
        oldest_ts = str(runs[-1].get("started_at", ""))
        try:
            oldest_dt = datetime.fromisoformat(oldest_ts.replace("Z", "+00:00"))
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
            window_days = max(0, (datetime.now(timezone.utc) - oldest_dt).days)
        except ValueError:
            window_days = None
    else:
        window_days = None

    return {
        "screen": "ops/runs",
        "requested_pack": requested_pack,
        "runs": runs,
        "day_groups": day_groups,
        "limit": cap,
        "window_days": window_days,
        "available": True,
    }



def build_signal_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    signal_type: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    governance_contract = describe_governance_contract(pack_name=pack_name)
    surface_contract = describe_observation_surface_contract(
        pack_name=pack_name,
        surface_kind="signals",
    )
    if surface_contract["status"] == "missing":
        return {
            "screen": "signals/browser",
            "requested_pack": requested_pack,
            "surface_contract": surface_contract,
            "governance_contract": governance_contract,
            "operator_rail": [],
            "surface_error": (
                f"Pack '{surface_contract['requested_pack']}' does not expose a shared shell "
                f"'signals' surface."
            ),
            "items": [],
            "count": 0,
            "query": query or "",
            "signal_type": signal_type or "",
            "type_counts": {},
            "impact_counts": {},
            "signal_type_explanations": SIGNAL_TYPE_EXPLANATIONS,
        }
    items = list_signals(vault_dir, pack_name=pack_name, signal_type=signal_type, query=query)
    return {
        "screen": "signals/browser",
        "requested_pack": requested_pack,
        "surface_contract": surface_contract,
        "governance_contract": governance_contract,
        "operator_rail": [
            _operator_action(
                "Action Queue",
                _scoped_path("/ops/actions", pack_name=requested_pack),
                "Run or inspect queued actions.",
            ),
            _operator_action(
                "Production Browser",
                _scoped_path("/ops/production", pack_name=requested_pack),
                "Trace current production weak points.",
            ),
            _operator_action(
                "Contradictions",
                _scoped_path(
                    "/ops/contradictions" if _supports_research_shell(pack_name) else "/search",
                    pack_name=requested_pack,
                ),
                (
                    "Review semantic tensions."
                    if _supports_research_shell(pack_name)
                    else "Shared-shell search fallback."
                ),
            ),
            _operator_action(
                "Orientation Brief",
                _scoped_path("/ops/briefing", pack_name=requested_pack),
                "Return to the current entry product.",
            ),
        ],
        "items": items,
        "count": len(items),
        "query": query or "",
        "signal_type": signal_type or "",
        "type_counts": dict(Counter(item["signal_type"] for item in items)),
        "impact_counts": _impact_counts(items),
        "capture_status_counts": _capture_status_counts(items),
        "signal_type_explanations": SIGNAL_TYPE_EXPLANATIONS,
    }



def build_stale_summary_browser_payload(
    vault_dir: Path | str,
    *,
    pack_name: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    requested_pack = pack_name or ""
    items = [
        {
            **item,
            "object_path": _scoped_path(
                f"/object?id={quote(str(item['object_id']), safe='')}",
                pack_name=requested_pack,
            ),
        }
        for item in list_stale_summaries(vault_dir, pack_name=pack_name, query=query)
    ]
    review_context = get_review_context(vault_dir, [item["object_id"] for item in items], pack_name=pack_name)
    return {
        "screen": "truth/stale-summaries",
        "requested_pack": requested_pack,
        "items": items,
        "count": len(items),
        "query": query or "",
        "review_context": review_context,
        "review_history": list_review_actions(vault_dir, object_ids=[item["object_id"] for item in items], limit=8),
        "detection_notes": [
            "Stale summary review flags compiled summaries that are weak and have no outgoing supporting relations.",
            "This queue is deterministic and favors false negatives over false positives.",
        ],
    }



def compute_today_staleness(
    vault_dir: Path | str, *, pack: str
) -> dict[str, Any]:
    """BL-103a: can the operator trust the daily numbers, or is a
    sync / projection rebuild outstanding?

    Two cheap, telemetry-free signals:

    * ``audit_sync_stale`` — ``pipeline.jsonl`` has a newer event
      than the newest row in ``knowledge.db.audit_events`` (the
      JSONL ledger advanced but the sync hasn't run).
    * ``projection_stale`` — ``ops_state`` was last refreshed BEFORE
      the newest synced audit row (audit is current but the
      lifecycle projection hasn't been rebuilt to reflect it).

    ``None`` for either flag means "could not determine" — the UI
    must say "run status unknown", never imply freshness it can't
    prove.
    """
    db_path = _db_path(vault_dir)
    jsonl_path = db_path.with_name("pipeline.jsonl")

    db_latest = None
    projection_at = None
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                # Stream a running max instead of materializing one
                # datetime per audit_events row (Codex review P2):
                # /ops/today runs this on every request and the
                # ledger grows unbounded on long-lived vaults
                # (~37k rows on the operator vault) — a full list
                # here reintroduces the exact page-load memory class
                # the recent bounded-rebuild work removed.
                for (ts,) in conn.execute(
                    "SELECT timestamp FROM audit_events"
                ):
                    parsed = _parse_audit_ts(str(ts or ""))
                    if parsed is not None and (
                        db_latest is None or parsed > db_latest
                    ):
                        db_latest = parsed
                has_ops = conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='ops_state'"
                ).fetchone()
                if has_ops:
                    prow = conn.execute(
                        "SELECT MAX(refreshed_at) FROM ops_state "
                        " WHERE pack = ?",
                        (pack,),
                    ).fetchone()
                    if prow and prow[0]:
                        projection_at = _parse_audit_ts(str(prow[0]))
        except sqlite3.Error:
            pass

    jsonl_latest = _jsonl_latest_ts(jsonl_path)
    slack = _dt.timedelta(seconds=_STALENESS_SLACK_SECONDS)

    # audit_sync_stale
    if jsonl_latest is None:
        audit_sync_stale: bool | None = None
    elif db_latest is None:
        # JSONL has events but nothing is synced.
        audit_sync_stale = True
    else:
        audit_sync_stale = jsonl_latest > db_latest + slack

    # projection_stale
    if db_latest is None:
        projection_stale: bool | None = None
    elif projection_at is None:
        # audit synced but no projection materialized yet.
        projection_stale = True
    else:
        projection_stale = projection_at < db_latest - slack

    if audit_sync_stale:
        summary = "audit_sync_stale"
        detail = (
            "pipeline.jsonl has newer events than knowledge.db — run "
            "`ovp-refresh-ops` before trusting today's counts."
        )
    elif projection_stale:
        summary = "projection_stale"
        detail = (
            "audit is synced but the lifecycle projection is older "
            "than it — run `ovp-ops-state --rebuild` (or "
            "`ovp-refresh-ops`)."
        )
    elif audit_sync_stale is None or projection_stale is None:
        summary = "unknown"
        detail = (
            "Could not determine freshness (missing pipeline.jsonl "
            "or knowledge.db); run status unknown."
        )
    else:
        summary = "current"
        detail = "Audit and lifecycle projection are current."

    def _iso(dt: Any) -> str:
        return dt.isoformat() if dt is not None else ""

    return {
        "summary": summary,
        "detail": detail,
        "audit_sync_stale": audit_sync_stale,
        "projection_stale": projection_stale,
        "jsonl_latest": _iso(jsonl_latest),
        "db_latest": _iso(db_latest),
        "projection_at": _iso(projection_at),
    }


__all__ = [
    '_activity_item_identity',
    '_briefing_value_check',
    '_build_dashboard_workflow_groups',
    '_build_evolution_section',
    '_build_latest_digest_info',
    '_build_note_jump_path',
    '_compute_v2_lineage',
    '_existing_object_rows',
    '_fetch_activity_rows',
    '_object_kind_profile',
    '_read_lifecycle_summary',
    '_search_note_type_label',
    '_source_excerpt_for_object',
    '_stage_runs_for_day',
    '_state_for_event_types',
    'build_action_queue_payload',
    'build_candidate_browser_payload',
    'build_contradiction_browser_payload',
    'build_event_dossier_payload',
    'build_intake_cohort_payload',
    'build_objects_index_payload',
    'build_production_browser_payload',
    'build_queue_overview_payload',
    'build_runs_index_payload',
    'build_signal_browser_payload',
    'build_stale_summary_browser_payload',
    'compute_today_staleness'
]
