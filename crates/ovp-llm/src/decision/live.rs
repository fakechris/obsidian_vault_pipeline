use std::io::Read;
use std::time::{Duration, Instant};

use super::*;
use reqwest::blocking::Client;
use reqwest::header::{AUTHORIZATION, HeaderMap, HeaderValue, RETRY_AFTER};

#[derive(Debug, Clone)]
pub struct HttpOptions {
    /// Total deadline including retries, sleeps and response-body reads.
    pub timeout: Duration,
    pub max_retries: u32,
    pub retry_backoff: Duration,
    pub max_response_bytes: u64,
    /// Explicit local mock/development server opt-in; never permits remote HTTP.
    pub allow_http_loopback: bool,
}
impl Default for HttpOptions {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(15),
            max_retries: 1,
            retry_backoff: Duration::from_millis(100),
            max_response_bytes: 4 * 1024 * 1024,
            allow_http_loopback: false,
        }
    }
}

/// Credentials live only in the HTTP client's sensitive header. Intentionally
/// has no Debug/Serialize impl. Redirects are disabled, including same-host ones.
pub struct TypeSafeDecisionClient {
    profile: DecisionProfile,
    client: Client,
    options: HttpOptions,
}
impl TypeSafeDecisionClient {
    pub fn from_env(profile: DecisionProfile, options: HttpOptions) -> Result<Self, DecisionError> {
        let key =
            std::env::var(&profile.credential_ref).map_err(|_| DecisionError::MissingCredential)?;
        Self::with_api_key(profile, key, options)
    }

    pub fn with_api_key(
        profile: DecisionProfile,
        key: String,
        options: HttpOptions,
    ) -> Result<Self, DecisionError> {
        typesafe::validate_profile(&profile)?;
        if options.timeout.is_zero()
            || options.timeout > Duration::from_secs(120)
            || options.max_retries > 3
            || options.retry_backoff > Duration::from_secs(5)
            || options.max_response_bytes == 0
            || options.max_response_bytes > 16 * 1024 * 1024
        {
            return Err(DecisionError::InvalidRequest("invalid HTTP limits"));
        }
        let url = reqwest::Url::parse(&profile.endpoint)
            .map_err(|_| DecisionError::InvalidRequest("invalid endpoint"))?;
        let loopback = url.host_str().is_some_and(|host| {
            host == "localhost"
                || host
                    .trim_matches(['[', ']'])
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|ip| ip.is_loopback())
        });
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.host_str().is_none()
            || !(url.scheme() == "https"
                || (options.allow_http_loopback && loopback && url.scheme() == "http"))
        {
            return Err(DecisionError::InvalidRequest(
                "endpoint must be HTTPS without embedded credentials, query or fragment",
            ));
        }
        if key.trim().is_empty() {
            return Err(DecisionError::MissingCredential);
        }
        let mut auth = HeaderValue::from_str(&format!("Bearer {key}"))
            .map_err(|_| DecisionError::MissingCredential)?;
        auth.set_sensitive(true);
        let mut headers = HeaderMap::new();
        headers.insert(AUTHORIZATION, auth);
        let client = Client::builder()
            .default_headers(headers)
            .redirect(reqwest::redirect::Policy::none())
            .timeout(options.timeout)
            .connect_timeout(options.timeout.min(Duration::from_secs(5)))
            .build()
            .map_err(|_| DecisionError::Transport)?;
        Ok(Self {
            profile,
            client,
            options,
        })
    }
}
impl DecisionClient for TypeSafeDecisionClient {
    fn profile(&self) -> &DecisionProfile {
        &self.profile
    }
    fn capabilities(&self) -> DecisionCapabilities {
        typesafe::CAPABILITIES
    }
    fn decide(&mut self, request: &DecisionRequest) -> Result<DecisionReply, DecisionError> {
        let body = typesafe::encode_request(&self.profile, request)?;
        let start = Instant::now();
        for attempt in 0..=self.options.max_retries {
            let remaining = self
                .options
                .timeout
                .checked_sub(start.elapsed())
                .filter(|d| !d.is_zero())
                .ok_or(DecisionError::Timeout)?;
            let response = self
                .client
                .post(&self.profile.endpoint)
                .timeout(remaining)
                .json(&body)
                .send();
            let mut retry_delay = self.options.retry_backoff;
            let error = match response {
                Ok(response) => {
                    let status = response.status();
                    if status.is_success() {
                        let mut bytes = vec![];
                        response
                            .take(self.options.max_response_bytes + 1)
                            .read_to_end(&mut bytes)
                            .map_err(|_| {
                                if start.elapsed() >= self.options.timeout {
                                    DecisionError::Timeout
                                } else {
                                    DecisionError::Transport
                                }
                            })?;
                        if bytes.len() as u64 > self.options.max_response_bytes {
                            return Err(DecisionError::InvalidReply("response too large"));
                        }
                        return typesafe::decode_reply(
                            &self.profile,
                            request,
                            &bytes,
                            start.elapsed().as_millis() as u64,
                            attempt + 1,
                        );
                    }
                    if status.as_u16() != 429 && !status.is_server_error() {
                        return Err(DecisionError::Http(status.as_u16()));
                    }
                    // Never retry earlier than a numeric Retry-After. An HTTP-date
                    // or malformed header is conservatively returned to the caller.
                    if let Some(value) = response.headers().get(RETRY_AFTER) {
                        let seconds = value
                            .to_str()
                            .ok()
                            .and_then(|s| s.parse::<u64>().ok())
                            .ok_or(DecisionError::Http(status.as_u16()))?;
                        retry_delay = retry_delay.max(Duration::from_secs(seconds));
                    }
                    DecisionError::Http(status.as_u16())
                }
                Err(error) if error.is_timeout() => DecisionError::Timeout,
                Err(_) => DecisionError::Transport,
            };
            if attempt == self.options.max_retries
                || start.elapsed().saturating_add(retry_delay) >= self.options.timeout
            {
                return Err(error);
            }
            std::thread::sleep(retry_delay);
        }
        unreachable!("bounded loop always returns")
    }
}
