# Parking Lot

Scoped-out ideas, future considerations, and open questions that don't belong in the current implementation.

---

## Infrastructure Decisions

### Cloud SQL: Private IP vs Public IP

**Chosen**: Private IP (no public IP) via VPC peering.

| | Private IP | Public IP + Authorized Networks |
|---|---|---|
| Security | DB unreachable from internet | Exposed on public IP, relies on IP allowlist |
| Setup complexity | Higher (VPC peering, Service Networking API, IP allocation) | Lower (just `--authorized-networks=CE_IP`) |
| Connectivity | Only same-VPC resources can connect | Any allowlisted IP can connect |
| Local dev access | Requires Cloud SQL Auth Proxy or SSH tunnel | Can allowlist dev machine IP |
| Cost | Same | Same |

**Rationale**: The one-time setup cost is paid once and fully scripted (`setup.sh` handles VPC peering automatically). The security benefit (DB completely off the internet) outweighs the minor complexity. Local dev uses a separate local PostgreSQL instance anyway, so the "harder to connect remotely" drawback doesn't apply.

**Revisit if**: We need direct DB access from local machines for debugging production data (solution: Cloud SQL Auth Proxy).

---

## Future Features (Architect For, Don't Implement)

### RAG-based Context Selection
Replace recent + random sampling with embedding-based retrieval for persona chat. Current design uses a `ContextProvider` interface that can be swapped without changing the API layer.

### Fine-tuned Model Backend
Swap Claude API context injection for a fine-tuned model per persona. The `ChatGenerator` interface supports this — just needs a new implementation.

### Time-of-Day Aware Responses
Post metadata already includes timestamps. Future prompt engineering could make personas respond differently based on time of day (e.g., more casual at night).

### Cloud SQL Auth Proxy for Local Dev
If production debugging requires connecting to Cloud SQL from local machines, set up the Auth Proxy. Not needed now since local dev runs its own Postgres.

---

## Open Questions

- **SSE in React Native**: Will `react-native-sse` or `eventsource` polyfill work reliably? Fallback: 2-second polling.
- **Firehose volume growth**: If Bluesky grows significantly, the e2-small instance may need upgrading. Monitor CPU/memory via Cloud Monitoring dashboard.
- **Persona corpus size**: Current 200-post limit fits Claude context window. If conversation quality needs improvement, consider increasing limit + truncation strategy.
