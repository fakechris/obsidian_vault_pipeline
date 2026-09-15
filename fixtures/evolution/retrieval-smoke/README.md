# Synthetic paired retrieval fixture

Two invented source metadata records and two manually specified gold questions.
The request-noise question exercises the already-existing verbatim/terms
policies; the other question has no answer. No customer material, external
benchmark, model reply or live capture is included.

The intentionally tiny fixture tests orchestration, source-rank recomputation,
positive/reversed/identity decisions and evidence persistence. It is not a
retrieval-quality benchmark and must not be used to tune production behavior.
