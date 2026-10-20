# Retry storm / cascading failure

**Mechanism.** Failures trigger retries at several layers; load multiplies, overloading a
dependency that was only slightly degraded, which fails more, which triggers more retries.

**Signature**
- Request rate to the struggling service *increases* while success rate falls.
- Requests per user action higher than normal; repeated identical requests in logs.
- Failure spreads from one tier to its callers over minutes.

**Confirm**
- `request_rate` of the dependency vs its callers' rate; ratio change at T0.
- Logs with retry attempt counters.

**Refute**
- Request rate flat or down during the failure.

**Notes.** A retry storm is usually an *amplifier*. Keep looking for the initial trigger.
