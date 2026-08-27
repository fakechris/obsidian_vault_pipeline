//! Short handles for the identifiers a model has to echo back.
//!
//! Both `cluster_select/v1` and `crystal_strength/v1` ask the model to return
//! identifiers it was given. Those identifiers are derived from source titles,
//! so in the live vault they look like:
//!
//! ```text
//! l3-2026-07-13_Claudate project-multilevel-index  🎼 Fractal self-referentia-4e7c3cc8-5
//! ```
//!
//! 85 characters, of which the first 84 are shared with every sibling claim in
//! the same cluster — emoji, double spaces and all. The prompts' own examples
//! use `agents-1`. Asking a model to transcribe the long form exactly is asking
//! it to do bookkeeping, and on 2026-08-23 it did not: the strength gate got
//! verdicts for `…-1`/`…-2` when it had asked about `…-5`/`…-6`, and a fresh
//! live sweep reproduced the same class on `cluster_select` — a selected case
//! id that was never in the offered set.
//!
//! Both gates then did the right thing and refused, which is why the weekly
//! crystallize job had been dead for four days.
//!
//! So the model never sees the long form. It gets `c1`, `c2`, … and the
//! mapping back is local and total. Two characters cannot be transposed into a
//! different-but-plausible identifier, and anything outside the table is still
//! rejected exactly as before — this shortens what the model must copy, it does
//! not loosen a single check.

use std::collections::BTreeMap;

/// Positional aliases for one model call.
///
/// Deterministic from the input order, so a caller can rebuild the same table
/// to resolve a reply without threading it through the request.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Aliases {
    to_real: BTreeMap<String, String>,
}

/// The alias for the item at `index` (0-based).
pub fn alias_for(index: usize) -> String {
    format!("c{}", index + 1)
}

/// Does this string occupy the handle namespace (`c` + digits)?
///
/// The namespace is reserved: a string of this shape is resolved by the table
/// or not at all, never by the real-id passthrough.
fn is_handle_shaped(s: &str) -> bool {
    let Some(rest) = s.strip_prefix('c') else {
        return false;
    };
    !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit())
}

impl Aliases {
    /// Build from real ids in the ORDER they are presented to the model.
    pub fn new<I, S>(real_ids: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let mut to_real = BTreeMap::new();
        for (i, id) in real_ids.into_iter().enumerate() {
            to_real.insert(alias_for(i), id.as_ref().to_string());
        }
        Self { to_real }
    }

    pub fn is_empty(&self) -> bool {
        self.to_real.is_empty()
    }

    pub fn len(&self) -> usize {
        self.to_real.len()
    }

    /// Resolve one identifier from a model reply.
    ///
    /// Accepts an alias, and ALSO passes a real id through unchanged: a model
    /// that quotes the long form back has still answered the question, and
    /// there is no reason to fail it for being verbose. Anything else returns
    /// `None` and the caller's existing validation rejects it — an alias that
    /// is not in the table must never resolve to "some id", which would be the
    /// wrong-identifier bug wearing a different hat.
    pub fn resolve<'a>(&'a self, given: &'a str) -> Option<&'a str> {
        let given = given.trim();
        if let Some(real) = self.to_real.get(given) {
            return Some(real.as_str());
        }
        // Anything SHAPED like a handle is judged only by the table. Without
        // this, a real id that happens to be `c9` would let an unoffered
        // handle `c9` through the passthrough below and be treated as that
        // item — the unoffered-identifier bug, wearing the new syntax.
        if is_handle_shaped(given) {
            return None;
        }
        self.to_real
            .values()
            .any(|r| r == given)
            .then_some(given)
    }

    /// Resolve a whole reply, keeping unresolvable entries VERBATIM so the
    /// caller's error message still names what the model actually said.
    pub fn resolve_all<'a, I: IntoIterator<Item = &'a str>>(&self, given: I) -> Vec<String> {
        given
            .into_iter()
            .map(|g| self.resolve(g).unwrap_or(g.trim()).to_string())
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table() -> Aliases {
        Aliases::new([
            "l3-2026-07-13_Claudate project-multilevel-index  🎼 Fractal self-referentia-4e7c3cc8-5",
            "l3-2026-07-13_Claudate project-multilevel-index  🎼 Fractal self-referentia-4e7c3cc8-6",
        ])
    }

    #[test]
    fn aliases_are_positional_and_short() {
        let a = table();
        assert_eq!(a.len(), 2);
        assert_eq!(
            a.resolve("c1"),
            Some("l3-2026-07-13_Claudate project-multilevel-index  🎼 Fractal self-referentia-4e7c3cc8-5")
        );
        // Two characters, against 85 with an 84-character shared prefix.
        assert_eq!(alias_for(0).len(), 2);
    }

    #[test]
    fn a_long_id_quoted_back_verbatim_still_resolves() {
        // Being verbose is not being wrong.
        let a = table();
        let real = "l3-2026-07-13_Claudate project-multilevel-index  🎼 Fractal self-referentia-4e7c3cc8-6";
        assert_eq!(a.resolve(real), Some(real));
    }

    #[test]
    fn an_unknown_alias_resolves_to_nothing_rather_than_to_something() {
        // The whole failure being fixed is an identifier that was never
        // offered. Resolving `c9` to "some id" would reintroduce it with a
        // shorter name.
        let a = table();
        assert_eq!(a.resolve("c9"), None);
        assert_eq!(a.resolve("l3-something-else-1"), None);
        assert_eq!(a.resolve(""), None);
    }

    #[test]
    fn a_handle_shaped_string_is_judged_only_by_the_table() {
        // If a real id happens to BE `c9`, the passthrough would otherwise
        // accept an unoffered handle `c9` and silently bind it to that item.
        let a = Aliases::new(["real-1", "c9"]);
        assert_eq!(a.resolve("c2"), Some("c9"), "c2 is the table entry for it");
        assert_eq!(a.resolve("c9"), None, "the handle namespace is reserved");
        assert_eq!(a.resolve("c99"), None);
        // A non-handle-shaped real id still passes through.
        assert_eq!(a.resolve("real-1"), Some("real-1"));
    }

    #[test]
    fn surrounding_whitespace_is_tolerated() {
        assert_eq!(table().resolve(" c1 ").is_some(), true);
    }

    #[test]
    fn resolve_all_keeps_an_unresolvable_entry_verbatim() {
        // The caller's error message has to be able to say what the model
        // actually returned; silently dropping it would hide the defect.
        let out = table().resolve_all(["c2", "c7"]);
        assert_eq!(out[1], "c7");
        assert!(out[0].ends_with("-6"));
    }
}
