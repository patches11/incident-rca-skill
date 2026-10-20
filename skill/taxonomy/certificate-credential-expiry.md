# Certificate / credential expiry

**Mechanism.** A TLS certificate, mTLS client cert, API key or token reaches its expiry time;
every new connection/request using it fails from that instant.

**Signature**
- Abrupt, near-total failure of one specific outbound or inbound path starting at a round
  wall-clock time (often :00), with **no change** recorded.
- Errors: `certificate has expired`, `CERTIFICATE_VERIFY_FAILED`, `tlsv1 alert certificate expired`,
  `401 invalid/expired credentials`, `notAfter=...`.
- Existing keep-alive connections may keep working for a while → errors ramp as connections recycle.

**Confirm**
- `search_logs(<svc>, "certificate")` / `"expired"` / `"x509"`; read the `notAfter` timestamp.
- Error onset time equals the expiry timestamp in the message.

**Refute**
- Errors not TLS/auth-related; or onset aligns with a deploy/config change instead.

**Fix.** Rotate/renew the credential (human, via secrets management). Add expiry monitoring.
**Not a code fix.**
