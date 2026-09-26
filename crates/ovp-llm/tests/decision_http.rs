#![cfg(feature = "decision-live")]
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use ovp_llm::decision::*;
use serde_json::{Value, json};

fn request() -> DecisionRequest {
    DecisionRequest {
        namespace: "http-contract/v1".into(),
        state: json!({"quote":"synthetic"}),
        evidence: vec![],
        questions: BTreeMap::from([(
            "ok".into(),
            DecisionQuestion {
                instructions: "Is the quote synthetic?".into(),
                kind: QuestionKind::Boolean {
                    yes: "yes".into(),
                    no: "no".into(),
                },
            },
        )]),
    }
}
fn response() -> String {
    json!({"model":"jev-1.13.0","answers":{"ok":{"type":"noul","noul":0.9}},"usage":{"input_tokens":11,"output_tokens":2}}).to_string()
}
fn profile(endpoint: String) -> DecisionProfile {
    DecisionProfile {
        id: "http-test".into(),
        provider: "typesafe".into(),
        endpoint,
        model: "jev-1.13.0".into(),
        credential_ref: "TEST_KEY".into(),
    }
}
type Captures = Arc<Mutex<Vec<Value>>>;
fn server(
    responses: Vec<(u16, String, String)>,
    delay: Duration,
) -> (String, Captures, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    let captures = Arc::new(Mutex::new(vec![]));
    let collected = captures.clone();
    let thread = std::thread::spawn(move || {
        for (status, extra, body) in responses {
            let started = Instant::now();
            let mut stream = loop {
                match listener.accept() {
                    Ok((s, _)) => break s,
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        assert!(
                            started.elapsed() < Duration::from_secs(3),
                            "expected mock call missing"
                        );
                        std::thread::sleep(Duration::from_millis(2));
                    }
                    Err(e) => panic!("accept failed: {e}"),
                }
            };
            // Accepted sockets can inherit the listener's nonblocking mode on
            // macOS. Only accept is polled; request reads must block with the
            // bounded timeout below.
            stream.set_nonblocking(false).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut bytes = vec![];
            let end = loop {
                let mut b = [0];
                stream.read_exact(&mut b).unwrap();
                bytes.push(b[0]);
                if bytes.ends_with(b"\r\n\r\n") {
                    break bytes.len();
                }
            };
            let headers = String::from_utf8(bytes.clone())
                .unwrap()
                .to_ascii_lowercase();
            assert!(headers.starts_with("post /v1/systemone "));
            assert!(headers.contains("authorization: bearer synthetic-test-key"));
            let size: usize = headers
                .lines()
                .find_map(|l| l.strip_prefix("content-length: "))
                .unwrap()
                .parse()
                .unwrap();
            bytes.resize(end + size, 0);
            stream.read_exact(&mut bytes[end..]).unwrap();
            collected
                .lock()
                .unwrap()
                .push(serde_json::from_slice(&bytes[end..]).unwrap());
            std::thread::sleep(delay);
            let text = format!(
                "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(text.as_bytes());
        }
    });
    (address, captures, thread)
}
fn options() -> HttpOptions {
    HttpOptions {
        timeout: Duration::from_secs(2),
        retry_backoff: Duration::ZERO,
        allow_http_loopback: true,
        ..HttpOptions::default()
    }
}

#[test]
fn transient_http_errors_retry_same_provider_and_report_attempts() {
    for status in [429, 503] {
        let (url, seen, thread) = server(
            vec![
                (
                    status,
                    "Retry-After: 0\r\n".into(),
                    "private echoed payload".into(),
                ),
                (200, String::new(), response()),
            ],
            Duration::ZERO,
        );
        let mut client = TypeSafeDecisionClient::with_api_key(
            profile(url),
            "synthetic-test-key".into(),
            options(),
        )
        .unwrap();
        let reply = client.decide(&request()).unwrap();
        thread.join().unwrap();
        assert_eq!(reply.receipt.network_attempts, 2);
        assert_eq!(reply.receipt.origin, DecisionOrigin::Live);
        let calls = seen.lock().unwrap();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0], calls[1]);
        assert_eq!(calls[0]["model"], "jev-1.13.0");
    }
}
#[test]
fn auth_redirect_and_decode_errors_do_not_retry_or_echo_bodies() {
    for (status, body) in [
        (401, "secret-private-echo".into()),
        (302, "secret-private-echo".into()),
        (200, "secret-private-echo".into()),
    ] {
        let (url, seen, thread) = server(
            vec![(
                status,
                "Location: https://example.invalid/\r\n".into(),
                body,
            )],
            Duration::ZERO,
        );
        let mut client = TypeSafeDecisionClient::with_api_key(
            profile(url),
            "synthetic-test-key".into(),
            options(),
        )
        .unwrap();
        let err = client.decide(&request()).unwrap_err();
        thread.join().unwrap();
        assert_eq!(seen.lock().unwrap().len(), 1);
        assert!(!err.to_string().contains("secret-private"));
        assert!(!err.to_string().contains("synthetic-test-key"));
        if status != 200 {
            assert_eq!(err, DecisionError::Http(status));
        }
    }
}
#[test]
fn retry_limit_and_retry_after_are_bounded() {
    let (url, seen, thread) = server(
        vec![
            (503, String::new(), "x".into()),
            (503, String::new(), "x".into()),
        ],
        Duration::ZERO,
    );
    let mut client =
        TypeSafeDecisionClient::with_api_key(profile(url), "synthetic-test-key".into(), options())
            .unwrap();
    assert_eq!(client.decide(&request()), Err(DecisionError::Http(503)));
    thread.join().unwrap();
    assert_eq!(seen.lock().unwrap().len(), 2);
    let (url, _, thread) = server(
        vec![(429, "Retry-After: 600\r\n".into(), "x".into())],
        Duration::ZERO,
    );
    let mut client =
        TypeSafeDecisionClient::with_api_key(profile(url), "synthetic-test-key".into(), options())
            .unwrap();
    let now = Instant::now();
    assert_eq!(client.decide(&request()), Err(DecisionError::Http(429)));
    assert!(now.elapsed() < Duration::from_secs(1));
    thread.join().unwrap();
}
#[test]
fn timeout_and_response_size_limit_are_enforced() {
    let (url, _, thread) = server(
        vec![(200, String::new(), response())],
        Duration::from_millis(200),
    );
    let opts = HttpOptions {
        timeout: Duration::from_millis(40),
        max_retries: 0,
        ..options()
    };
    let mut client =
        TypeSafeDecisionClient::with_api_key(profile(url), "synthetic-test-key".into(), opts)
            .unwrap();
    assert_eq!(client.decide(&request()), Err(DecisionError::Timeout));
    thread.join().unwrap();
    let (url, _, thread) = server(vec![(200, String::new(), response())], Duration::ZERO);
    let opts = HttpOptions {
        max_response_bytes: 20,
        ..options()
    };
    let mut client =
        TypeSafeDecisionClient::with_api_key(profile(url), "synthetic-test-key".into(), opts)
            .unwrap();
    assert_eq!(
        client.decide(&request()),
        Err(DecisionError::InvalidReply("response too large"))
    );
    thread.join().unwrap();
}
#[test]
fn credentials_and_insecure_endpoints_fail_before_network() {
    assert!(matches!(
        TypeSafeDecisionClient::with_api_key(
            profile("https://example.invalid/v1/systemone".into()),
            String::new(),
            options()
        ),
        Err(DecisionError::MissingCredential)
    ));
    for endpoint in [
        "http://example.invalid/v1/systemone",
        "https://secret@example.invalid/v1/systemone",
        "https://example.invalid/v1/systemone?key=secret",
    ] {
        assert!(
            TypeSafeDecisionClient::with_api_key(
                profile(endpoint.into()),
                "synthetic-test-key".into(),
                options()
            )
            .is_err()
        );
    }
    assert!(
        TypeSafeDecisionClient::with_api_key(
            profile("http://127.0.0.1:9999/v1/systemone".into()),
            "synthetic-test-key".into(),
            HttpOptions::default()
        )
        .is_err()
    );
}
