#![cfg(feature = "anthropic")]
use ovp_llm::{
    anthropic::AnthropicBlockingClient,
    decision::{
        chat::{CAPABILITIES, ChatDecisionClient, MAX_TOKENS},
        *,
    },
};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::TcpListener,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

struct Server {
    url: String,
    seen: Arc<Mutex<Vec<Value>>>,
    stop: Arc<AtomicBool>,
    task: Option<thread::JoinHandle<()>>,
}
impl Server {
    fn new(status: u16, extra: String, body: String, delay: Duration) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}/v1/messages", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(vec![]));
        let captured = seen.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let task = thread::spawn(move || {
            while !stopping.load(Ordering::SeqCst) {
                let mut stream = match listener.accept() {
                    Ok((s, _)) => s,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(e) => panic!("accept: {e}"),
                };
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut headers = vec![];
                while !headers.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    stream.read_exact(&mut byte).unwrap();
                    headers.push(byte[0]);
                    assert!(headers.len() < 65536);
                }
                let headers = String::from_utf8(headers).unwrap().to_ascii_lowercase();
                assert!(headers.starts_with("post /v1/messages "));
                assert!(headers.contains("x-api-key: synthetic-test-key"));
                let len: usize = headers
                    .lines()
                    .find_map(|l| l.strip_prefix("content-length: "))
                    .unwrap()
                    .parse()
                    .unwrap();
                let mut bytes = vec![0; len];
                stream.read_exact(&mut bytes).unwrap();
                captured
                    .lock()
                    .unwrap()
                    .push(serde_json::from_slice(&bytes).unwrap());
                thread::sleep(delay);
                let response = format!(
                    "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        Self {
            url,
            seen,
            stop,
            task: Some(task),
        }
    }
    fn count(&self) -> usize {
        self.seen.lock().unwrap().len()
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(task) = self.task.take() {
            task.join().unwrap();
        }
    }
}
fn profile(endpoint: String) -> DecisionProfile {
    DecisionProfile {
        id: "chat-http-test".into(),
        provider: "chat".into(),
        endpoint,
        model: "fixed-1".into(),
        credential_ref: "TEST_KEY".into(),
    }
}
fn request() -> DecisionRequest {
    DecisionRequest {
        namespace: "chat-http/v1".into(),
        state: json!({"quote":"synthetic"}),
        evidence: vec![],
        questions: BTreeMap::from([(
            "ok".into(),
            DecisionQuestion {
                instructions: "Is the quote synthetic?".into(),
                kind: QuestionKind::Boolean {
                    yes: "synthetic".into(),
                    no: "not synthetic".into(),
                },
            },
        )]),
    }
}
fn response(model: &str) -> String {
    json!({"model":model,"content":[{"type":"text","text":r#"{"answers":{"ok":{"type":"boolean","value":true}}}"#}],"stop_reason":"end_turn","usage":{"input_tokens":12,"output_tokens":7}}).to_string()
}
fn client(server: &Server, timeout: Duration) -> ChatDecisionClient {
    let live = AnthropicBlockingClient::new("synthetic-test-key")
        .with_base_url(&server.url)
        .with_decision_transport(timeout, true)
        .unwrap();
    ChatDecisionClient::new(profile(server.url.clone()), Box::new(live)).unwrap()
}
#[test]
fn record_cache_hit_and_replay_make_exactly_one_http_exchange() {
    let server = Server::new(200, String::new(), response("fixed-1"), Duration::ZERO);
    let dir = tempfile::tempdir().unwrap();
    let mut record = CachedDecisionClient::record(
        Box::new(client(&server, Duration::from_secs(2))),
        dir.path(),
    )
    .unwrap();
    let live = record.decide(&request()).unwrap();
    assert_eq!(live.receipt.origin, DecisionOrigin::Live);
    assert_eq!(live.receipt.network_attempts, 1);
    assert_eq!(
        live.receipt.evaluation_usage.as_ref().unwrap().input_tokens,
        12
    );
    let hit = record.decide(&request()).unwrap();
    assert_eq!(hit.receipt.origin, DecisionOrigin::Cache);
    assert_eq!(hit.receipt.network_attempts, 0);
    let mut replay =
        CachedDecisionClient::replay(profile(server.url.clone()), CAPABILITIES, dir.path())
            .unwrap();
    let replayed = replay.decide(&request()).unwrap();
    assert_eq!(replayed.receipt.origin, DecisionOrigin::Replay);
    assert_eq!(replayed.receipt.network_attempts, 0);
    assert_eq!(replayed.answers, live.answers);
    assert_eq!(
        replayed.receipt.evaluation_usage,
        live.receipt.evaluation_usage
    );
    assert_eq!(server.count(), 1);
    let captured = server.seen.lock().unwrap();
    assert_eq!(captured[0]["model"], "fixed-1");
    assert_eq!(captured[0]["max_tokens"], MAX_TOKENS);
    assert!(captured[0].get("tools").is_none());
}
#[test]
fn redirect_is_not_followed_and_error_has_no_private_body() {
    let destination = Server::new(200, String::new(), response("fixed-1"), Duration::ZERO);
    let server = Server::new(
        307,
        format!("Location: {}\r\n", destination.url),
        "secret-private-body".into(),
        Duration::ZERO,
    );
    let err = client(&server, Duration::from_secs(2))
        .decide(&request())
        .unwrap_err();
    assert_eq!(err, DecisionError::Transport);
    assert!(!err.to_string().contains("secret"));
    assert_eq!(server.count(), 1);
    assert_eq!(destination.count(), 0);
}
#[test]
fn model_mismatch_and_timeout_fail_without_retry() {
    let server = Server::new(200, String::new(), response("other-model"), Duration::ZERO);
    assert_eq!(
        client(&server, Duration::from_secs(2)).decide(&request()),
        Err(DecisionError::InvalidReply("model mismatch"))
    );
    assert_eq!(server.count(), 1);
    let slow = Server::new(
        200,
        String::new(),
        response("fixed-1"),
        Duration::from_millis(300),
    );
    let started = Instant::now();
    assert_eq!(
        client(&slow, Duration::from_millis(70)).decide(&request()),
        Err(DecisionError::Transport)
    );
    assert!(started.elapsed() < Duration::from_secs(2));
    assert_eq!(slow.count(), 1);
}
