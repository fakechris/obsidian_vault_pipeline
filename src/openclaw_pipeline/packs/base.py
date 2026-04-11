from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObjectKindSpec:
    kind: str
    display_name: str
    description: str
    canonical: bool = True
    schema_ref: str | None = None


@dataclass(frozen=True)
class WorkflowProfile:
    name: str
    description: str
    stages: list[str]
    supports_autopilot: bool = False


@dataclass(frozen=True)
class StageHandlerSpec:
    stage: str
    description: str = ""


@dataclass
class BaseDomainPack:
    name: str
    version: str
    api_version: int
    _object_kinds: list[ObjectKindSpec] = field(default_factory=list)
    _workflow_profiles: list[WorkflowProfile] = field(default_factory=list)
    _discoverable_object_kinds: list[str] = field(default_factory=list)
    _extraction_profiles: list[object] = field(default_factory=list)
    _operation_profiles: list[object] = field(default_factory=list)
    _wiki_views: list[object] = field(default_factory=list)

    def object_kinds(self) -> list[ObjectKindSpec]:
        return list(self._object_kinds)

    def workflow_profiles(self) -> list[WorkflowProfile]:
        return list(self._workflow_profiles)

    def discoverable_object_kinds(self) -> list[str]:
        if self._discoverable_object_kinds:
            return list(self._discoverable_object_kinds)
        return [item.kind for item in self._object_kinds]

    def profile(self, name: str) -> WorkflowProfile:
        for profile in self._workflow_profiles:
            if profile.name == name:
                return profile
        raise ValueError(f"Unknown workflow profile '{name}' for pack '{self.name}'")

    def extraction_profiles(self) -> list[object]:
        return list(self._extraction_profiles)

    def extraction_profile(self, name: str) -> object:
        for profile in self._extraction_profiles:
            if getattr(profile, "name", None) == name:
                return profile
        raise ValueError(f"Unknown extraction profile '{name}' for pack '{self.name}'")

    def operation_profiles(self) -> list[object]:
        return list(self._operation_profiles)

    def operation_profile(self, name: str) -> object:
        for profile in self._operation_profiles:
            if getattr(profile, "name", None) == name:
                return profile
        raise ValueError(f"Unknown operation profile '{name}' for pack '{self.name}'")

    def wiki_views(self) -> list[object]:
        return list(self._wiki_views)

    def wiki_view(self, name: str) -> object:
        for view in self._wiki_views:
            if getattr(view, "name", None) == name:
                return view
        raise ValueError(f"Unknown wiki view '{name}' for pack '{self.name}'")
