"""Source-authority signal providers.

A SignalProvider answers a single question for a source URL + frontmatter:
"based on this provider's evidence, how authoritative is this source?"

The orchestrator (``source_authority.py``) collects signals from every
registered provider that ``applies()`` and combines them into a single
0-1 ``authority`` score, plus a structured ``signals`` dict for audit.

Providers fall in 4 tiers (rough cost/reliability ordering):

  T1 deterministic, free       — domain_rules, author_rules
  T2 free public APIs          — github, arxiv
  T3 rate-limited / paid       — twitter, substack (stubs; see docstrings)
  T4 LLM-judged                — content_quality (deferred)

Each provider is independent.  Adding a new signal source = drop a
module here implementing ``SignalProvider`` and register it.
"""

from .base import SignalProvider, Signal, AuthorityScore
from .domain_rules import DomainRulesProvider
from .author_rules import AuthorRulesProvider
from .github import GitHubSignalProvider
from .arxiv import ArxivSignalProvider
from .twitter import TwitterSignalProvider  # stub
from .substack import SubstackSignalProvider  # stub

__all__ = [
    "SignalProvider",
    "Signal",
    "AuthorityScore",
    "DomainRulesProvider",
    "AuthorRulesProvider",
    "GitHubSignalProvider",
    "ArxivSignalProvider",
    "TwitterSignalProvider",
    "SubstackSignalProvider",
]
