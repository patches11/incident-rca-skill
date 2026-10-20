# What didn't work

Running log of things that failed, were wrong, or had to be redone — and what changed as a result.

1. **MCP SDK v2 API drift.** Planned on `mcp.server.fastmcp.FastMCP`; the installed SDK (2.3.0) renamed it to `mcp.server.mcpserver.MCPServer`. Small, but a reminder that AI-written scaffolding targets the API it remembers.
2. **Verifier accepted fabricated small numbers.** First version matched a quoted number against *any* number in the cited result. Timestamps (`2026-10-14T...`), version strings (`2026.10.14-1`) and ids (`q004`) meant almost any small integer "verified". Fixed by stripping timestamps/versions/ids before numeric matching; unit test `test_timestamps_do_not_satisfy_small_numbers` guards it.
3. **Verifier didn't detect a tampered audit result.** Re-run hash was compared with the *stored hash*, not the hash of the stored result, so editing the result in the audit log went unnoticed. Now all three must agree. Test: `test_tampered_audit_detected_on_rerun`.
4. **Docstring + string concatenation isn't a docstring.** `"""...""" + TIME_HELP` silently produced tool descriptions with no text (Python only treats a bare literal as `__doc__`). Caught by inspecting `tools/list`.
5. **Answer leakage via the report template.** The first template's example YAML was the pool-exhaustion answer (service, commit). Replaced with a neutral example.
