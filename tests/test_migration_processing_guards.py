from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openclaw_pipeline import auto_github_processor as github_module
from openclaw_pipeline import auto_paper_processor as paper_module
from openclaw_pipeline.auto_article_processor import (
    AutoArticleProcessor,
    LiteLLMClient as ArticleLiteLLMClient,
    PipelineLogger as ArticleLogger,
    TransactionManager as ArticleTxn,
)
from openclaw_pipeline.auto_github_processor import LiteLLMClient as GithubLiteLLMClient
from openclaw_pipeline.auto_github_processor import parse_github_url
from openclaw_pipeline.auto_paper_processor import LiteLLMClient as PaperLiteLLMClient, process_single_paper
from openclaw_pipeline.lint_checker import KnowledgeLinter
from openclaw_pipeline.markdown_generation import sanitize_generated_markdown
from openclaw_pipeline.unified_pipeline_enhanced import (
    EnhancedPipeline,
    PipelineLogger,
    TransactionManager,
)


def test_pinboard_process_routes_paper_like_website_to_paper_processor(temp_vault, monkeypatch):
    pinboard_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_arxiv.org.md"
    pinboard_file.parent.mkdir(parents=True, exist_ok=True)
    pinboard_file.write_text(
        """---
title: "[2505.22954] Darwin Godel Machine"
source: https://arxiv.org/abs/2505.22954
date: 2026-04-07
type: pinboard-website
tags: [paper]
---

[2505.22954] Darwin Godel Machine
""",
        encoding="utf-8",
    )

    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(temp_vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(temp_vault, logger, txn)

    captured: list[list[str]] = []

    def fake_run(cmd, capture_output, text, cwd, timeout, env=None):
        captured.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("openclaw_pipeline.unified_pipeline_enhanced.subprocess.run", fake_run)

    result = pipeline.step_pinboard_process(dry_run=False)

    assert result["processed"] == 1
    assert "openclaw_pipeline.auto_paper_processor" in " ".join(captured[0])
    assert captured[0]


def test_parse_github_url_accepts_blob_tree_and_release_urls():
    assert parse_github_url("https://github.com/andelf/picc/blob/master/src/bin/voice_correct.rs") == ("andelf", "picc")
    assert parse_github_url("https://github.com/Yeachan-Heo/oh-my-codex/tree/main") == ("Yeachan-Heo", "oh-my-codex")
    assert parse_github_url("https://github.com/hyperspaceai/agi/releases/tag/architect-v1") == ("hyperspaceai", "agi")


def test_pinboard_process_uses_extended_subprocess_timeout_for_slow_github_repos(temp_vault, monkeypatch):
    pinboard_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_example_github.md"
    pinboard_file.parent.mkdir(parents=True, exist_ok=True)
    pinboard_file.write_text(
        """---
title: "example/repo"
source: https://github.com/example/repo
date: 2026-04-07
type: pinboard-github
tags: [tool]
---

example/repo
""",
        encoding="utf-8",
    )

    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(temp_vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(temp_vault, logger, txn)

    captured: list[int] = []

    def fake_run(cmd, capture_output, text, cwd, timeout, env=None):
        captured.append(timeout)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("openclaw_pipeline.unified_pipeline_enhanced.subprocess.run", fake_run)

    result = pipeline.step_pinboard_process(dry_run=False)

    assert result["processed"] == 1
    assert captured == [600]


def test_pipeline_subprocesses_include_project_src_on_pythonpath(temp_vault, monkeypatch):
    pinboard_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_example.md"
    pinboard_file.parent.mkdir(parents=True, exist_ok=True)
    pinboard_file.write_text(
        """---
title: "example/repo"
source: https://github.com/example/repo
date: 2026-04-07
type: pinboard-github
tags: [tool]
---

example/repo
""",
        encoding="utf-8",
    )

    logger = PipelineLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn = TransactionManager(temp_vault / "60-Logs" / "transactions")
    pipeline = EnhancedPipeline(temp_vault, logger, txn)

    captured_envs: list[dict[str, str]] = []

    def fake_run(cmd, capture_output, text, cwd, timeout, env):
        captured_envs.append(env)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("openclaw_pipeline.unified_pipeline_enhanced.subprocess.run", fake_run)

    result = pipeline.step_pinboard_process(dry_run=False)

    assert result["processed"] == 1
    assert captured_envs
    assert str((Path(__file__).resolve().parents[1] / "src")) in captured_envs[0]["PYTHONPATH"]


def test_sanitize_generated_markdown_unwraps_fenced_frontmatter():
    raw = """```yaml
---
title: Example
source: https://example.com
---

# Body
```"""

    cleaned = sanitize_generated_markdown(raw)

    assert cleaned.startswith("---\n")
    assert "```yaml" not in cleaned
    assert cleaned.rstrip().endswith("# Body")


def test_pinboard_processor_rejects_cross_day_cli_range(tmp_path):
    script = Path(__file__).resolve().parents[1] / "pinboard-processor.py"
    env = os.environ.copy()
    env["PINBOARD_TOKEN"] = "user:token"
    env["WIGS_VAULT_DIR"] = str(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--start-date",
            "2026-04-01",
            "--end-date",
            "2026-04-02",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "不支持跨天范围查询" in result.stderr


def test_article_processor_abstains_when_only_metadata_is_available(temp_vault, monkeypatch):
    raw_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_arxiv.org.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(
        """---
title: "OSGym: Scalable OS Infra for Computer Use Agents"
source: https://arxiv.org/pdf/2511.11672
author: unknown
date: 2026-04-07
type: pinboard-website
tags: [paper]
---

OSGym: Scalable OS Infra for Computer Use Agents

## Notes


## Tags

#paper
""",
        encoding="utf-8",
    )

    logger = ArticleLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn = ArticleTxn(temp_vault / "60-Logs" / "transactions")
    processor = AutoArticleProcessor(temp_vault, logger, txn)
    processor.article_processor = SimpleNamespace(
        generate_interpretation=lambda **_: (_ for _ in ()).throw(AssertionError("LLM should not be called"))
    )

    monkeypatch.setattr(
        "openclaw_pipeline.image_downloader.ImageDownloader.process_file",
        lambda self, file_path, backup=True: [],
    )

    result = processor.process_single_file(raw_file, dry_run=False)

    assert result["status"] == "skipped"
    assert result["error"] == "paper_source_requires_paper_processor"
    assert result["output_path"] is None


def test_article_processor_can_promote_docs_page_when_primary_page_is_thin(temp_vault, monkeypatch):
    raw_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_example.com.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(
        """---
title: "Example SDK"
source: https://example.com
author: unknown
date: 2026-04-07
type: pinboard-website
tags: [sdk]
---

Example SDK
""",
        encoding="utf-8",
    )

    logger = ArticleLogger(temp_vault / "60-Logs" / "pipeline.jsonl")
    txn = ArticleTxn(temp_vault / "60-Logs" / "transactions")
    processor = AutoArticleProcessor(temp_vault, logger, txn)
    processor.article_processor = SimpleNamespace(
        generate_interpretation=lambda **kwargs: ("---\ntitle: Test\nsource: x\nauthor: y\ndate: 2026-04-07\ntype: article\ntags: []\nstatus: draft\n---\n\n# ok", {"tokens": 1}, "tools")
    )

    monkeypatch.setattr(
        "openclaw_pipeline.image_downloader.ImageDownloader.process_file",
        lambda self, file_path, backup=True: [],
    )

    class FakeResponse:
        def __init__(self, text: str, content_type: str = "text/html", status_code: int = 200):
            self.text = text
            self.status_code = status_code
            self.headers = {"Content-Type": content_type}
            self.content = text.encode("utf-8")

    docs_text = "<html><body><article>" + ("Detailed docs content. " * 120) + "</article></body></html>"
    homepage = '<html><body><h1>Example SDK</h1><a href="/docs/getting-started">Documentation</a></body></html>'

    def fake_get(url, timeout=15, headers=None, allow_redirects=True):
        if url == "https://example.com":
            return FakeResponse(homepage)
        if url == "https://example.com/docs/getting-started":
            return FakeResponse(docs_text)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("openclaw_pipeline.auto_article_processor.requests.get", fake_get)

    result = processor.process_single_file(raw_file, dry_run=False)

    assert result["status"] == "completed"
    assert result["classification"] == "tools"


def test_linter_resolve_link_handles_dot_relative_target_without_crashing(temp_vault):
    note = temp_vault / "20-Areas" / "AI-Research" / "Topics" / "2026-04-07_Test.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        """---
title: Test
date: 2026-04-07
type: note
---

[Broken](./.md)
""",
        encoding="utf-8",
    )

    linter = KnowledgeLinter(temp_vault)
    linter.scan()

    assert linter._resolve_link(".") is None


def test_processors_default_to_auto_vault_model_env(monkeypatch):
    monkeypatch.setenv("AUTO_VAULT_MODEL", "minimax/MiniMax-M2.7-highspeed")
    monkeypatch.setenv("AUTO_VAULT_API_BASE", "https://api.minimaxi.com/anthropic")
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "test-key")

    article_client = ArticleLiteLLMClient()
    paper_client = PaperLiteLLMClient()
    github_client = GithubLiteLLMClient()

    assert article_client.model == "anthropic/MiniMax-M2.7-highspeed"
    assert paper_client.model == "anthropic/MiniMax-M2.7-highspeed"
    assert github_client.model == "anthropic/MiniMax-M2.7-highspeed"


def test_processors_fall_back_to_minimax_api_key_env(monkeypatch):
    monkeypatch.delenv("AUTO_VAULT_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-key")
    monkeypatch.setenv("AUTO_VAULT_MODEL", "minimax/MiniMax-M2.7-highspeed")
    monkeypatch.setenv("AUTO_VAULT_API_BASE", "https://api.minimaxi.com/anthropic")

    paper_client = PaperLiteLLMClient()
    github_client = GithubLiteLLMClient()
    article_client = ArticleLiteLLMClient()

    assert paper_client._api_key == "minimax-key"
    assert github_client._api_key == "minimax-key"
    assert article_client._api_key == "minimax-key"


def test_github_cli_does_not_override_auto_vault_model_when_flag_is_omitted(temp_vault, monkeypatch):
    pinboard_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_example.md"
    pinboard_file.parent.mkdir(parents=True, exist_ok=True)
    pinboard_file.write_text(
        """---
title: "example/repo"
source: https://github.com/example/repo
date: 2026-04-07
tags: [tool]
---
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class StubLLM:
        def __init__(self, *, model=None, api_type="anthropic", api_key=None, api_base=None):
            captured["model"] = model
            self.model = "stub-model"
            self.total_calls = 0

    monkeypatch.setattr(github_module, "LiteLLMClient", StubLLM)
    monkeypatch.setattr(
        github_module,
        "process_single_repo",
        lambda **kwargs: {"status": "completed", "tokens_used": 0},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auto_github_processor.py",
            "--process-single",
            str(pinboard_file),
            "--vault-dir",
            str(temp_vault),
        ],
    )

    assert github_module.main() == 0
    assert captured["model"] is None


def test_paper_cli_does_not_override_auto_vault_model_when_flag_is_omitted(temp_vault, monkeypatch):
    pinboard_file = temp_vault / "50-Inbox" / "02-Pinboard" / "2026-04-07_paper.md"
    pinboard_file.parent.mkdir(parents=True, exist_ok=True)
    pinboard_file.write_text(
        """---
title: "[2505.22954] Example Paper"
source: https://arxiv.org/abs/2505.22954
date: 2026-04-07
tags: [paper]
---
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class StubLLM:
        def __init__(self, *, model=None, api_type="anthropic", api_key=None, api_base=None):
            captured["model"] = model
            self.model = "stub-model"
            self.total_calls = 0

    monkeypatch.setattr(paper_module, "LiteLLMClient", StubLLM)
    monkeypatch.setattr(
        paper_module,
        "process_single_paper",
        lambda **kwargs: {"status": "completed", "tokens_used": 0},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "auto_paper_processor.py",
            "--process-single",
            str(pinboard_file),
            "--vault-dir",
            str(temp_vault),
        ],
    )

    assert paper_module.main() == 0
    assert captured["model"] is None


@pytest.mark.parametrize(("client_class", "patch_mode"), [
    (ArticleLiteLLMClient, "article"),
    (PaperLiteLLMClient, "module"),
    (GithubLiteLLMClient, "module"),
])
def test_litellm_clients_retry_transient_failures(monkeypatch, client_class, patch_mode):
    monkeypatch.setenv("AUTO_VAULT_API_KEY", "test-key")
    monkeypatch.setenv("AUTO_VAULT_MODEL", "minimax/MiniMax-M2.7-highspeed")

    attempts = {"count": 0}

    class FakeUsage:
        total_tokens = 42

    class FakeMessage:
        content = "ok"

    class FakeChoice:
        message = FakeMessage()
        finish_reason = "stop"

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    def fake_completion(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient upstream 404")
        return FakeResponse()

    if patch_mode == "article":
        monkeypatch.setattr("openclaw_pipeline.auto_article_processor.litellm.completion", fake_completion)
    else:
        monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    client = client_class()
    content, metadata = client.generate("system", "user")

    assert content == "ok"
    assert metadata["tokens"] == 42
    assert attempts["count"] == 2


def test_process_single_paper_downloads_remote_pdf_before_analysis(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self, content: bytes):
            self.status_code = 200
            self.content = content
            self.headers = {"Content-Type": "application/pdf"}

    monkeypatch.setattr(
        "openclaw_pipeline.auto_paper_processor.requests.get",
        lambda url, timeout=30: FakeResponse(b"%PDF-1.4 fake"),
    )

    extracted_paths: list[str] = []

    def fake_extract_pdf_text(pdf_path: str, max_chars: int = 10000) -> str:
        extracted_paths.append(pdf_path)
        return "Remote paper body " * 200

    monkeypatch.setattr("openclaw_pipeline.auto_paper_processor.extract_pdf_text", fake_extract_pdf_text)

    class StubLLM:
        def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 8000):
            assert "Remote paper body" in user_prompt
            return ("---\ntitle: Paper\nsource: x\nauthor: y\ndate: 2026-04-07\ntype: paper\ntags: []\n---\n\n# Paper", {"tokens": 1})

    output_dir = tmp_path / "papers"
    result = process_single_paper(
        source="https://example.com/paper.pdf",
        title="Remote PDF Paper",
        authors=[],
        date="2026-04-07",
        llm_client=StubLLM(),
        output_dir=output_dir,
        dry_run=False,
    )

    assert result["status"] == "completed"
    assert extracted_paths
    assert output_dir.exists()
