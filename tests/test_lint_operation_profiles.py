from __future__ import annotations


def test_lint_issue_can_be_mapped_to_operation_proposal():
    from openclaw_pipeline.lint_checker import LintIssue, issue_to_operation_proposal

    proposal = issue_to_operation_proposal(
        LintIssue(
            layer="L2",
            level="warning",
            type="frontmatter-missing-title",
            file="10-Knowledge/Evergreen/Example.md",
            message="Missing title",
            suggestion="Add a title field",
            auto_fixable=False,
        )
    )

    assert proposal["queue_name"] == "frontmatter"
    assert proposal["proposal_type"] == "lint_issue"
    assert proposal["review_required"] is True
    assert proposal["file"] == "10-Knowledge/Evergreen/Example.md"

