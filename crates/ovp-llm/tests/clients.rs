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
