# Media Security

## Trust model

All uploaded media is untrusted until server-side validation succeeds. The user controls filename, declared MIME, bytes, embedded metadata, captions/transcript text, and potentially adversarial visual/audio content. Direct storage upload avoids API bandwidth exposure; it does not establish content safety.

## Mandatory validation sequence

1. Confirm authorized tenant/session/immutable object identity and expected size through the storage port.
2. Verify server-observed SHA-256 and compare it with the completion contract.
3. Identify content by signature/container inspection; compare client declaration only as an assertion, never proof.
4. Apply allowed type/container, duration, size, stream, and decompression/parser policy limits.
5. Run the malware-scanning port and persist a minimal safe verdict/provenance.
6. Only a clean, policy-compliant asset may enter FFprobe, derivative, ASR, or VLM queues.

Malformed or policy-disallowed media is rejected. Infected or security-indeterminate media is quarantined and may only be handled by explicitly authorized operations procedures. Scanner unavailability is a controlled retry/dead-letter condition, never a clean result.

## Worker isolation

Media binaries run in an isolated, least-privilege worker with fixed binaries, argument arrays, constrained temporary storage, and resource quotas. No user-controlled value is concatenated into a shell command. Network access is denied unless a specific adapter requires it; storage/provider access is least-privilege and short-lived. Inputs/outputs have bounded size, process trees are terminated on timeout, and cleanup is reliable under cancellation/retry.

## Data protection and observability

Originals and derivatives live in server-generated tenant namespaces and are immutable. Signed URLs, credentials, tokens, raw media metadata, raw scan/provider payloads, and sensitive text never appear in logs, Problem Details, or audit details. Structured telemetry uses safe IDs, status/error code, capability, duration, attempt, and correlation ID. Audit trails record security-relevant transition facts without sensitive payloads.

## Security testing

Required tests include MIME/extension spoofing, checksum mismatch, corrupt container, oversize/duration boundaries, malicious scan fixture, scanner outage, shell-metacharacter filename, parser/output-limit exhaustion, prompt injection embedded in transcript/frame text, duplicate delivery, tenant IDOR, deletion/cancellation during execution, and signed-URL/token redaction.
