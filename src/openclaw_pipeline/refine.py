from __future__ import annotations

import json
import os
import re
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .identity import canonicalize_note_id
from .runtime import resolve_vault_dir


def load_note_targets(vault_dir: Path, slug: str | None = None, all_notes: bool = False) -> list[Path]:
    """Resolve cleanup/breakdown targets from the Evergreen directory."""
    evergreen_dir = resolve_vault_dir(vault_dir) / "10-Knowledge" / "Evergreen"
    if not evergreen_dir.exists():
        return []

    if all_notes:
        return sorted(f for f in evergreen_dir.glob("*.md") if not f.name.startswith("_"))

    if not slug:
        return []

    exact = evergreen_dir / f"{slug}.md"
    if exact.exists():
        return [exact]

    for note in evergreen_dir.glob("*.md"):
        if read_note_slug(note) == slug:
            return [note]
    return []


def read_frontmatter(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---") or yaml is None:
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def read_note_slug(path: Path) -> str:
    metadata = read_frontmatter(path)
    return canonicalize_note_id(str(metadata.get("note_id") or path.stem))


def read_note_document(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---") or yaml is None:
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except Exception:
        metadata = {}
    body = parts[2].lstrip("\n")
    return metadata, body


def render_note_document(metadata: dict[str, Any], body: str) -> str:
    if not metadata or yaml is None:
        return body.rstrip() + "\n"
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{frontmatter}\n---\n\n{body.rstrip()}\n"


def extract_h2_sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            if current_title is not None:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
            continue
        if current_title is not None:
            current_lines.append(line)
    if current_title is not None:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return sections


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def refresh_canonical_after_refine(vault_dir: Path) -> dict[str, Any]:
    """Reconcile registry and refresh Atlas after deterministic refine writes."""
    from .auto_moc_updater import MOCUpdater, PipelineLogger
    from .concept_registry import ConceptRegistry
    from .rebuild_registry import reconcile_registry

    resolved_vault = resolve_vault_dir(vault_dir)
    with redirect_stdout(StringIO()):
        reconcile_result = reconcile_registry(resolved_vault, write=True, verbose=False)

    updater = MOCUpdater(resolved_vault, PipelineLogger(resolved_vault / "60-Logs" / "pipeline.jsonl"))
    atlas_result = updater.update_atlas_from_registry(dry_run=False)

    post_registry = ConceptRegistry(resolved_vault).load()
    post_registry_slugs = {entry.slug for entry in post_registry.entries}
    fs_slugs = set(reconcile_result.get("fs_slugs", []))
    registry_synced = fs_slugs.issubset(post_registry_slugs)
    atlas_refreshed = not atlas_result.get("errors")
    return {
        "canonical_refreshed": registry_synced and atlas_refreshed,
        "registry_synced": registry_synced,
        "atlas_refreshed": atlas_refreshed,
        "registry_result": reconcile_result,
        "atlas_result": atlas_result,
    }


def record_refine_run(
    vault_dir: Path,
    *,
    mode: str,
    mutations: list[dict[str, Any]],
    targets: list[str],
    write: bool,
    canonical_refresh: dict[str, Any] | None = None,
) -> None:
    layout = resolve_vault_dir(vault_dir)
    session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
    timestamp = datetime.now().isoformat()
    logs_dir = layout / "60-Logs"
    mutation_log = logs_dir / "refine-mutations.jsonl"
    pipeline_log = logs_dir / "pipeline.jsonl"

    for mutation in mutations:
        if mutation.get("status") != "written":
            continue
        _append_jsonl(
            mutation_log,
            {
                "timestamp": timestamp,
                "session_id": session_id,
                "event_type": "refine_mutation_applied",
                "mode": mode,
                **mutation,
            },
        )

    _append_jsonl(
        pipeline_log,
        {
            "timestamp": timestamp,
            "session_id": session_id,
            "event_type": "refine_run_completed",
            "mode": mode,
            "write": write,
            "targets": targets,
            "mutation_count": len(mutations),
            "applied_count": sum(1 for mutation in mutations if mutation.get("status") == "written"),
            "canonical_refreshed": bool(canonical_refresh and canonical_refresh.get("canonical_refreshed")),
            "registry_synced": bool(canonical_refresh and canonical_refresh.get("registry_synced")),
            "atlas_refreshed": bool(canonical_refresh and canonical_refresh.get("atlas_refreshed")),
        },
    )


def attach_proposal_evidence(vault_dir: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    from .evidence import build_evidence_payload

    enriched = dict(proposal)
    enriched["evidence"] = build_evidence_payload(
        vault_dir,
        query=str(proposal.get("slug") or ""),
        mentions=[str(proposal.get("slug") or "")],
        slugs=[str(proposal.get("slug") or "")],
        limit=5,
    )
    return enriched


def _rewrite_cleanup_body(body: str) -> str:
    lines = body.splitlines()
    rewritten: list[str] = []
    inserted_history = False
    date_header_pattern = re.compile(r"^##\s+(\d{4}-\d{2}(?:-\d{2})?)$")

    for line in lines:
        if date_header_pattern.match(line):
            if not inserted_history:
                if rewritten and rewritten[-1] != "":
                    rewritten.append("")
                rewritten.append("## Historical Notes")
                rewritten.append("")
                inserted_history = True
            rewritten.append(re.sub(r"^##", "###", line, count=1))
        else:
            rewritten.append(line)

    return "\n".join(rewritten).rstrip() + "\n"


def execute_cleanup(path: Path, proposal: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
    mutation = {
        "mode": "cleanup",
        "slug": proposal["slug"],
        "action": proposal["action"],
        "path": str(path),
        "write": write,
        "status": "skipped",
    }
    if proposal["action"] != "cleanup_rewrite":
        mutation["status"] = "no_change"
        return mutation

    metadata, body = read_note_document(path)
    rewritten_body = _rewrite_cleanup_body(body)
    if rewritten_body == body:
        mutation["status"] = "no_change"
        return mutation

    if write:
        path.write_text(render_note_document(metadata, rewritten_body), encoding="utf-8")
        mutation["status"] = "written"
    else:
        mutation["status"] = "planned"
    return mutation


def _build_child_note(parent_slug: str, child_slug: str, title: str, section_body: str) -> str:
    metadata = {
        "note_id": child_slug,
        "title": title,
        "type": "evergreen",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "derived_from": parent_slug,
    }
    body = f"# {title}\n\n> Derived from [[{parent_slug}]]\n\n{section_body.strip()}\n"
    return render_note_document(metadata, body)


def _upsert_derived_notes_section(body: str, child_slugs: list[str]) -> str:
    section = "## Derived Notes\n\n" + "\n".join(f"- [[{slug}]]" for slug in child_slugs)
    pattern = re.compile(r"(?ms)^## Derived Notes\s*\n.*?(?=^##\s|\Z)")
    if pattern.search(body):
        updated = pattern.sub(section + "\n\n", body)
    else:
        updated = body.rstrip() + "\n\n" + section + "\n"
    return updated.rstrip() + "\n"


def execute_breakdown(vault_dir: Path, path: Path, proposal: dict[str, Any], *, write: bool = False) -> dict[str, Any]:
    mutation = {
        "mode": "breakdown",
        "slug": proposal["slug"],
        "action": proposal["action"],
        "path": str(path),
        "write": write,
        "status": "skipped",
        "created_children": [],
    }
    if proposal["action"] != "split":
        mutation["status"] = "no_change"
        return mutation

    metadata, body = read_note_document(path)
    sections = extract_h2_sections(body)
    if not sections:
        mutation["status"] = "no_change"
        return mutation

    evergreen_dir = resolve_vault_dir(vault_dir) / "10-Knowledge" / "Evergreen"
    created_children: list[str] = []
    for (title, section_body), child_slug in zip(sections, proposal["proposed_children"]):
        child_path = evergreen_dir / f"{child_slug}.md"
        if not child_path.exists() and write:
            child_path.write_text(
                _build_child_note(proposal["slug"], child_slug, title, section_body),
                encoding="utf-8",
            )
        created_children.append(str(child_path))

    updated_body = _upsert_derived_notes_section(body, proposal["proposed_children"])
    if write:
        path.write_text(render_note_document(metadata, updated_body), encoding="utf-8")
        mutation["status"] = "written"
    else:
        mutation["status"] = "planned"
    mutation["created_children"] = created_children
    return mutation


def analyze_cleanup(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    slug = read_note_slug(path)

    diary_headers = re.findall(r"^##\s+\d{4}-\d{2}", content, re.MULTILINE)
    action = "cleanup_rewrite" if diary_headers or len(lines) > 25 else "no_change"
    reasons = []
    if diary_headers:
        reasons.append("contains date-driven section headings")
    if len(lines) > 25:
        reasons.append("note is long enough to benefit from thematic restructuring")
    if not reasons:
        reasons.append("no cleanup trigger detected")

    return {
        "decision_type": "rewrite_decision",
        "mode": "cleanup",
        "slug": slug,
        "action": action,
        "confidence": 0.82 if action != "no_change" else 0.55,
        "reasons": reasons,
        "path": str(path),
    }


def analyze_breakdown(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    h2_titles = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
    h2_count = len(h2_titles)
    slug = read_note_slug(path)

    action = "split" if len(lines) > 35 or h2_count >= 2 else "keep"
    reasons = []
    if len(lines) > 35:
        reasons.append("note exceeds breakdown length threshold")
    if h2_count >= 2:
        reasons.append("multiple major sections suggest separable subtopics")
    if not reasons:
        reasons.append("no breakdown trigger detected")

    proposed_children = []
    if action == "split":
        proposed_children = [
            f"{slug}-{canonicalize_note_id(title)}"
            for title in h2_titles[:2]
            if canonicalize_note_id(title)
        ] or [f"{slug}-subtopic"]

    return {
        "decision_type": "split_decision",
        "mode": "breakdown",
        "slug": slug,
        "action": action,
        "confidence": 0.84 if action == "split" else 0.51,
        "reasons": reasons,
        "proposed_children": proposed_children,
        "path": str(path),
    }
