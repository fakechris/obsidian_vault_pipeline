# No interpretation expected for this fixture.

The legacy pipeline ingested this github repo, enriched it with deepwiki (31 sections,
216KB of content), wrote it to `50-Inbox/03-Processed/`, and **stopped there** — no
`_深度解读.md` was ever produced. The raw exists on its own as the terminal state.

This is captured as a fixture deliberately. It documents that "raw without interpretation"
is a real and legal state in the legacy system. The new system must decide:

- **Option A** (replicate): treat github as a routing decision `StopAtRaw` — enriched
  raws are valuable on their own and need no interpretation step.
- **Option B** (improve): build a `GithubRepoInterpreter` that turns the deepwiki
  content into a structured project-overview note. This is net-new behavior, not
  a contract break.

See `../notes.md` for the recommendation and the related open question.
