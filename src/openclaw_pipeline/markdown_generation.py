from __future__ import annotations


def sanitize_generated_markdown(content: str) -> str:
    text = content.strip()
    for fence in ("```markdown", "```md", "```yaml", "```"):
        if text.startswith(fence):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                inner = "\n".join(lines[1:-1]).strip()
                if inner.startswith("---"):
                    return inner + "\n"
    return content
