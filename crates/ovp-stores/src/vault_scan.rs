use std::path::Path;

/// Recursively walk `root` for `*.md` files, returning `(vault-relative
/// path, content)` pairs sorted by path. Domain-blind I/O — used by
/// derived-state rebuilds (e.g. knowledge-index backlink scanning) that
/// then parse the content with domain logic.
///
/// A missing root yields an empty list (nothing to scan), not an error.
/// Paths use forward slashes for stable, OS-independent output.
pub fn walk_markdown(root: &Path) -> std::io::Result<Vec<(String, String)>> {
    let mut out = Vec::new();
    if root.exists() {
        walk_inner(root, root, &mut out)?;
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(out)
}

fn walk_inner(
    root: &Path,
    dir: &Path,
    out: &mut Vec<(String, String)>,
) -> std::io::Result<()> {
    for entry in std::fs::read_dir(dir)? {
        let path = entry?.path();
        if path.is_dir() {
            walk_inner(root, &path, out)?;
        } else if path.extension().and_then(|e| e.to_str()) == Some("md") {
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let content = std::fs::read_to_string(&path)?;
            out.push((rel, content));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn walks_recursively_sorted_md_only() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join("a/b")).unwrap();
        std::fs::write(tmp.path().join("a/one.md"), "1").unwrap();
        std::fs::write(tmp.path().join("a/b/two.md"), "2").unwrap();
        std::fs::write(tmp.path().join("a/ignore.txt"), "x").unwrap();

        let files = walk_markdown(tmp.path()).unwrap();
        let paths: Vec<&str> = files.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(paths, vec!["a/b/two.md", "a/one.md"]);
    }

    #[test]
    fn missing_root_is_empty() {
        assert!(walk_markdown(Path::new("/no/such/dir")).unwrap().is_empty());
    }
}
