from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..extraction.artifacts import write_run_result
from ..extraction.runtime import ExtractionRuntime
from ..packs.loader import load_pack
from ..runtime import VaultLayout, resolve_vault_dir


class NoopExtractor:
    def extract(self, chunk_text, *, chunk_index, source_path, profile):  # noqa: ANN001, ARG002
        return []


def build_extractor() -> object:
    return NoopExtractor()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a pack extraction profile and emit derived artifacts.")
    parser.add_argument("--vault-dir", type=Path, default=None, help="Vault directory")
    parser.add_argument("--pack", default="default-knowledge", help="Pack name")
    parser.add_argument("--profile", required=True, help="Extraction profile name")
    parser.add_argument("--source", type=Path, required=True, help="Source markdown/text file")
    args = parser.parse_args(argv)

    vault_dir = resolve_vault_dir(args.vault_dir)
    pack = load_pack(args.pack)
    profile = pack.extraction_profile(args.profile)
    source_path = args.source.resolve()
    text = source_path.read_text(encoding="utf-8")

    runtime = ExtractionRuntime(extractor=build_extractor())
    result = runtime.run_text(profile=profile, text=text, source_path=source_path)
    artifact_path = write_run_result(VaultLayout.from_vault(vault_dir), result)

    print(json.dumps({"artifact_path": str(artifact_path), "profile_name": result.profile_name}, ensure_ascii=False))
    return 0
