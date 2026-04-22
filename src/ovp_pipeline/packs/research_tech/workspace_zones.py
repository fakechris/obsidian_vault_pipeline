"""research-tech workspace zones (Phase 34).

Codifies the state-not-authorship boundary:

* ``agent_owned``: drafts, briefings, inbox — agents may write freely with
  provenance frontmatter (state: draft/derived).
* ``accepted``: Plan/Roadmap/Decisions/MOC/Evergreen — gated by promotion.
* ``append_only``: ``00-Polaris/Writing-Prompts.md`` — declared exception
  inside the accepted zone (Phase 36 query feedback appends here).
"""

from __future__ import annotations

from ..base import WorkspaceZonesSpec


RESEARCH_TECH_WORKSPACE_ZONES = WorkspaceZonesSpec(
    agent_owned=(
        "20-Areas/**",
        "30-Projects/*/Drafts/**",
        "30-Projects/*/Briefings/**",
        "30-Projects/*/_OVP-Inbox/**",
        "50-Inbox/**",
        "60-Logs/**",
    ),
    accepted=(
        "00-Polaris/**",
        "10-Knowledge/**",
        "30-Projects/*/Plan.md",
        "30-Projects/*/Roadmap.md",
        "30-Projects/*/Decisions.md",
        "30-Projects/*/README.md",
    ),
    append_only=(
        "00-Polaris/Writing-Prompts.md",
        "60-Logs/**",
    ),
)
