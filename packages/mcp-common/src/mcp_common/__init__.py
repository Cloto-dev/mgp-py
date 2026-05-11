"""Common utilities shared across Magic Gateway Protocol (MGP) Python servers.

v0.2.0 introduces the foundation layer ported from
``cloto-mcp-servers/servers/common/``:

- :mod:`mcp_common.validation` — graceful-degradation argument validators
  (``validate_bool`` / ``validate_str`` / ``validate_int`` / ``validate_dict`` /
  ``validate_float`` / ``validate_list``).
- :mod:`mcp_common.isolation` — γ-semantics two-tier isolation helpers
  shared by CPersona & CScheduler (``gamma_clause`` / ``coerce_for_*`` /
  ``extract_axes_for_*``).
- :mod:`mcp_common.no_persist` — session no-persist mode helpers
  (``pause`` / ``resume`` / ``status`` / ``is_paused`` /
  ``make_skipped_response``).
- :mod:`mcp_common.mgp_utils` — MGP capability builder and stream-notification
  emitters (``MgpCapabilities`` / ``send_mgp_stream_chunk`` /
  ``write_mgp_*``).

Additional modules (mcp tooling, embedding client, semantic cache, search,
mcp stream interceptor, LLM provider) are deferred to subsequent Phase 2
sub-PRs.
"""

__version__ = "0.2.0"
