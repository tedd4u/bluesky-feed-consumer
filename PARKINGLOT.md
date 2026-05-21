# Parking Lot

Ideas scoped out, open questions, potential issues, and things to consider for future iterations.

---

## Scoped-Out Features

- **Configurable stats dimensions/facts via API** — currently hard-coded. Could allow API consumers to define custom aggregations.
- **Flexible query API for stats** — arbitrary time ranges and custom aggregations. Currently fixed windows only.
- **RAG-based context selection for persona chat** — embed persona posts, retrieve semantically relevant ones per user message. Currently using recent + sampled strategy.
- **Fine-tuned model backend** — train small LMs (TinyLlama, Phi-2, etc.) on persona post history for self-hosted generation. Currently using Claude API with context injection. Interface is modular for future swap.
- **Time-of-day aware persona responses** — timestamp metadata is already included in context. Future work: prompt engineering to make persona respond differently at night vs morning (match their real posting patterns).
- **Language detection fallback** — currently using Bluesky's own language tags. Could add fasttext as fallback for untagged posts if "other" bucket gets too large.

## Open Technical Questions

- **SSE vs polling for React Native** — SSE is the plan, but if `react-native-sse` or `eventsource` polyfill has issues with reconnection on mobile network transitions, fall back to 2-second polling. Test early.
- **Persona context metadata** — currently including: timestamp, post-vs-reply, parent post text. Other candidates: engagement metrics (weight popular posts higher), thread context. How much metadata is too much? Test prompt quality with varying metadata levels.
- **Quote post context depth** — currently including the persona's comment + brief reference to quoted content. Should we include the full quoted post text? More context for the model but more tokens consumed.
- **Top replied-to posts** — removed from initial stats dashboard (only Top Liked and Top Reposted now). Could add as a third segment option in the future.
- **Post history fetching** — AT Protocol API pagination and rate limits need investigation. How fast can we pull 200 posts for a new persona? Does Bluesky rate-limit this?
- **Claude API model choice** — which Claude model for persona chat? Haiku for cost, Sonnet for quality? Budget allows either at demo scale.
- **Stats data retention** — how long to keep old window snapshots in Postgres? Indefinitely? Rolling 24h? Needs a retention policy eventually.

## Scaling Considerations (if this goes beyond demo)

- Firehose consumer would need horizontal scaling (partition by event type or DID range)
- Stats aggregation would need distributed counters (Redis, or shard by time window)
- Persona chat at scale: prompt caching for frequently-chatted personas, connection pooling for Claude API
- API auth would need upgrade to OAuth/JWT with rate limiting
- Database would need connection pooling (pgbouncer), read replicas for stats queries
- Consider separating stats reads onto a read replica to isolate from chat writes
- GPU instances for fine-tuned models: ~$200-400/mo per instance, need model registry and serving pipeline

## UX Ideas

- Show "typing..." indicator during streaming persona responses
- Persona "mood" indicator based on recent post sentiment
- Stats dashboard: tap a top-N post to open it in the Bluesky feed
- Persona chat: show which real posts influenced a response (like citations)
- "Compare personas" mode: same prompt sent to multiple persona models side-by-side
