# General on-call runbook (all services)

1. Acknowledge the page. Post in #incidents with the alert link.
2. Check the dashboard for the paging service: request rate, error rate, latency (RED).
3. Check dependencies listed in the service catalog — the symptom is often not where the cause is.
4. Check recent deploys and config changes (`recent_changes`) for the paging service *and* its dependencies.
5. Mitigation options require a human with prod access and the incident commander's OK:
   - roll back the last deploy (`deployctl rollback <service>`)
   - revert a config change (via the config repo PR, never by hand-editing prod)
   - scale out (`deployctl scale <service> --replicas N`)
6. Write a postmortem within 5 business days for any SEV1/SEV2. Add new org-specific quirks to `org-context/overlay.md`.
