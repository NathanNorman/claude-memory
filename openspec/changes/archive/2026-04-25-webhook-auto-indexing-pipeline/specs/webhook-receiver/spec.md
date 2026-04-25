## ADDED Requirements

### Requirement: Accept GitHub push webhooks
The webhook receiver SHALL expose a `POST /webhook` endpoint that accepts GitHub push event payloads. The receiver SHALL extract the repository name, clone URL, before SHA, after SHA, and ref from the payload. The receiver SHALL return HTTP 202 Accepted after enqueueing the job.

#### Scenario: Valid push event to default branch
- **WHEN** GitHub sends a POST to `/webhook` with a valid push event payload for the `refs/heads/main` branch and a valid HMAC-SHA256 signature
- **THEN** the receiver enqueues an index job with the repo name, clone URL, before SHA, after SHA, and ref, and returns HTTP 202

#### Scenario: Push event to non-default branch
- **WHEN** GitHub sends a POST to `/webhook` with a push event for a branch other than `main` or `master`
- **THEN** the receiver returns HTTP 200 with a message indicating the branch was skipped and does NOT enqueue a job

#### Scenario: Non-push event type
- **WHEN** GitHub sends a POST to `/webhook` with an event type other than `push` (e.g., `pull_request`, `issues`)
- **THEN** the receiver returns HTTP 200 with a message indicating the event was ignored

### Requirement: Verify webhook signatures
The webhook receiver SHALL verify the HMAC-SHA256 signature in the `X-Hub-Signature-256` header against the shared secret before processing any payload. Requests with missing or invalid signatures SHALL be rejected.

#### Scenario: Valid signature
- **WHEN** a request arrives with an `X-Hub-Signature-256` header that matches the HMAC-SHA256 of the request body using the configured secret
- **THEN** the receiver processes the payload normally

#### Scenario: Missing signature header
- **WHEN** a request arrives without an `X-Hub-Signature-256` header
- **THEN** the receiver returns HTTP 401 Unauthorized

#### Scenario: Invalid signature
- **WHEN** a request arrives with an `X-Hub-Signature-256` header that does NOT match the expected HMAC-SHA256
- **THEN** the receiver returns HTTP 401 Unauthorized

### Requirement: Health check endpoint
The webhook receiver SHALL expose a `GET /health` endpoint that returns HTTP 200 with a JSON body indicating service status.

#### Scenario: Health check
- **WHEN** a GET request is sent to `/health`
- **THEN** the receiver returns HTTP 200 with `{"status": "ok"}`
