from __future__ import annotations

from ..base import WorkflowProfile


DEFAULT_KNOWLEDGE_FULL_PROFILE = WorkflowProfile(
    name="full",
    description="Default knowledge full pipeline",
    stages=[
        "pinboard",
        "pinboard_process",
        "clippings",
        "articles",
        "quality",
        "fix_links",
        "absorb",
        "registry_sync",
        "moc",
        "knowledge_index",
    ],
)

DEFAULT_KNOWLEDGE_AUTOPILOT_PROFILE = WorkflowProfile(
    name="autopilot",
    description="Default knowledge autopilot runtime",
    stages=[
        "interpretation",
        "quality",
        "absorb",
        "moc",
        "knowledge_index",
    ],
    supports_autopilot=True,
)
