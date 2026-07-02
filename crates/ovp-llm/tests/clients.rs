//! Integration tests for ModelClient impls.
//!
//! Coverage:
//! - FixtureModelClient: hit + miss
//! - NeverCallsClient: always errors
//! - CachedModelClient(Record): inner is called once, second hit serves from cache
//! - CachedModelClient(ReplayOnly): cache miss errors without touching inner
//! - CachedModelClient: cassette survives across instances (file persistence)

use ovp_llm::*;

fn req(text: &str) -> ModelRequest {
    ModelRequest {
        model: "test-model".into(),
        system: Some("you are a tester".into()),
        messages: vec![ModelMessage::User { content: text.into() }],
        max_tokens: 100,
        temperature: None,
        cache_namespace: None,
    }
}

fn reply(text: &str) -> ModelReply {
    ModelReply {
        model: "test-model".into(),
        text: text.into(),
        stop_reason: StopReason::EndTurn,
        usage: Usage { input_tokens: 5, output_tokens: 10 },
    }
}

/// A request tagged with a per-request cache namespace + distinct content.
fn req_ns(text: &str, namespace: &str) -> ModelRequest {
    req(text).with_cache_namespace(namespace)
}

#[test]
fn one_client_namespaces_article_and_paper_without_collision() {
    // Regression for the unified-pipeline bug: a single CachedModelClient
    // (shared by one LLMInvoker) must file article and paper cassettes
    // under their own prompt namespaces, from the per-request hint, not
    // the fixed constructor namespace.
    let article = req_ns("article body", "article_interpret/v1");
    let paper = req_ns("paper body", "paper_interpret/v1");

    let mut inner = FixtureModelClient::new();
    inner.insert(&article, reply("article-reply"));
    inner.insert(&paper, reply("paper-reply"));

    let tmp = tempfile::tempdir().unwrap();
    // Constructor namespace is a deliberately-wrong fallback; the
    // per-request hint must win.
    {
        let mut cached =
            CachedModelClient::new(inner, tmp.path(), "fallback", CacheMode::Record).unwrap();
        assert_eq!(cached.call(&article).unwrap().text, "article-reply");
        assert_eq!(cached.call(&paper).unwrap().text, "paper-reply");
    }

    // Each landed under its own prompt namespace, not "fallback".
    let article_dir = tmp.path().join("article_interpret/v1");
    let paper_dir = tmp.path().join("paper_interpret/v1");
    assert_eq!(std::fs::read_dir(&article_dir).unwrap().count(), 1, "article cassette");
    assert_eq!(std::fs::read_dir(&paper_dir).unwrap().count(), 1, "paper cassette");
    assert!(!tmp.path().join("fallback").exists(), "fallback dir must be unused");

    // A fresh replay-only client (NeverCalls inside) replays BOTH from
    // their namespaced dirs with no network and no collision.
    let mut replay =
        CachedModelClient::new(NeverCallsClient, tmp.path(), "fallback", CacheMode::ReplayOnly)
            .unwrap();
    assert_eq!(replay.call(&article).unwrap().text, "article-reply");
    assert_eq!(replay.call(&paper).unwrap().text, "paper-reply");
}

#[test]
fn fixture_hit_and_miss() {
    let mut client = FixtureModelClient::new();
    client.insert(&req("hi"), reply("hello"));

    let got = client.call(&req("hi")).expect("hit");
    assert_eq!(got.text, "hello");

    let miss = client.call(&req("unseen"));
    assert!(matches!(miss, Err(CallError::CacheMiss { .. })));
}

#[test]
fn never_calls_always_errors() {
    let mut client = NeverCallsClient;
    let result = client.call(&req("x"));
    match result {
        Err(CallError::Unexpected { detail }) => assert!(detail.contains("NeverCallsClient")),
        other => panic!("expected Unexpected, got {other:?}"),
    }
}

#[test]
fn cached_record_then_serves_from_memo() {
    let mut inner = FixtureModelClient::new();
    inner.insert(&req("hi"), reply("from-inner"));

    let tmp = tempfile::tempdir().unwrap();
    let mut cached = CachedModelClient::new(inner, tmp.path(), "", CacheMode::Record).unwrap();

    // First call hits the inner, records to disk.
    let r1 = cached.call(&req("hi")).expect("first call ok");
    assert_eq!(r1.text, "from-inner");

    // Cassette file must exist on disk now.
    let entries: Vec<_> = std::fs::read_dir(tmp.path()).unwrap().collect();
    assert_eq!(entries.len(), 1, "exactly one cassette written");

    // Second call serves from memo (we can't directly observe this, but it
    // should still return the same reply — and would still work even if
    // we swapped the inner client for NeverCallsClient mid-flight).
    let r2 = cached.call(&req("hi")).expect("second call ok");
    assert_eq!(r2.text, "from-inner");
}

#[test]
fn cached_replay_only_misses_dont_hit_inner() {
    let tmp = tempfile::tempdir().unwrap();
    let mut cached =
        CachedModelClient::new(NeverCallsClient, tmp.path(), "", CacheMode::ReplayOnly).unwrap();

    let result = cached.call(&req("anything"));
    match result {
        Err(CallError::CacheMiss { key }) => assert_eq!(key.len(), 64, "sha256 hex key"),
        other => panic!("expected CacheMiss, got {other:?}"),
    }
}

#[test]
fn cached_record_persists_across_instances() {
    let tmp = tempfile::tempdir().unwrap();

    // First instance: record.
    {
        let mut inner = FixtureModelClient::new();
        inner.insert(&req("hi"), reply("recorded-value"));
        let mut cached = CachedModelClient::new(inner, tmp.path(), "", CacheMode::Record).unwrap();
        cached.call(&req("hi")).unwrap();
    }

    // Second instance: replay-only, NeverCallsClient inside. Should still
    // find the recorded reply on disk.
    let mut replay =
        CachedModelClient::new(NeverCallsClient, tmp.path(), "", CacheMode::ReplayOnly).unwrap();
    let r = replay.call(&req("hi")).expect("replay finds disk cassette");
    assert_eq!(r.text, "recorded-value");
}

/// Returns scripted replies in order (last one repeats), counting calls.
struct Scripted {
    replies: Vec<&'static str>,
    calls: usize,
}

impl ModelClient for Scripted {
    fn call(&mut self, _r: &ModelRequest) -> Result<ModelReply, CallError> {
        let text = self.replies[self.calls.min(self.replies.len() - 1)];
        self.calls += 1;
        Ok(reply(text))
    }
}

#[test]
fn record_invalidate_forgets_and_rerecords() {
    // The retry-pinning fix: a recorded reply whose CONTENT proved unusable is
    // invalidated, so the next call re-asks the inner client and re-records.
    let tmp = tempfile::tempdir().unwrap();
    let inner = Scripted { replies: vec!["bad-reply", "good-reply"], calls: 0 };
    let mut cached =
        CachedModelClient::new(inner, tmp.path(), "ns/v1", CacheMode::Record).unwrap();

    let r = req("x");
    assert_eq!(cached.call(&r).unwrap().text, "bad-reply");
    assert_eq!(cached.call(&r).unwrap().text, "bad-reply", "pinned by the cassette");

    cached.invalidate(&r);
    assert_eq!(cached.call(&r).unwrap().text, "good-reply", "re-asked after invalidate");
    assert_eq!(cached.call(&r).unwrap().text, "good-reply", "new reply re-recorded");
}

#[test]
fn replay_only_invalidate_never_deletes_fixtures() {
    let tmp = tempfile::tempdir().unwrap();
    {
        let mut inner = FixtureModelClient::new();
        inner.insert(&req("hi"), reply("committed-fixture"));
        let mut record =
            CachedModelClient::new(inner, tmp.path(), "ns/v1", CacheMode::Record).unwrap();
        record.call(&req("hi")).unwrap();
    }

    // A replay-only cache serves (possibly committed) fixtures; a failing run
    // calling invalidate must not be able to delete them.
    let mut replay =
        CachedModelClient::new(NeverCallsClient, tmp.path(), "ns/v1", CacheMode::ReplayOnly)
            .unwrap();
    replay.invalidate(&req("hi"));
    assert_eq!(replay.call(&req("hi")).unwrap().text, "committed-fixture");
}
