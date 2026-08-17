# MRTR migration path — getting off the single-replica constraint

**Date:** 2026-08-16
**Status:** design note. **Tracks A and C1 are implemented** (§3, §5); B, C2 and C3 are not.
**Related:** `2026-08-15-forensic-audit.md` (M3), `2026-08-16-remediation-record.md` §1.1

---

## 0. The finding that reframes this

The prompt for this note was: *MCP went stateless in the 2026-07-28 spec, and Multi
Round-Trip Requests look like the answer to the approval gate that blocks a
coroutine for 300 seconds — so migrate to it.*

Having read the spec and the code, that is half right, and the half that is wrong
matters more.

**MRTR fixes the MCP hop. It does not unpin the backend from one replica.**

The single-replica constraint (finding M3) is not caused by MCP session state. It
is caused by three things the backend holds in process memory that MCP has no
opinion about:

| State | Type | Serialisable? |
|---|---|---|
| `orchestrations[user_id]` | live Magentic workflow object | **No** |
| `active_tasks[user_id]` | `asyncio.Task` | **No** |
| `sockets[user_id]` | live `WebSocket` | **No** — inherently per-process |
| `_approval_events`, `_clarification_events` | `asyncio.Event` | No, but replaceable |
| `approvals`, `clarifications`, `plans` | plain dicts | Yes |
| `_approval_owners`, `_clarification_owners` | plain dicts | Yes |

*(`src/backend/orchestration/connection_config.py:40–57`)*

Adopting MRTR and changing nothing else would leave every row of that table exactly
as it is. A second replica would still fail the same way: an approval posted to
instance B never reaches the coroutine awaiting it on instance A, and the plan hangs
at the gate with no error.

So this note splits into three tracks that are **independent** and have different
blockers. Track A is available today. Track B is blocked on upstream. Track C is the
one that actually unpins the replicas, and it is the largest.

---

## 1. What MRTR actually is

From the [2026-07-28 specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
(SEP-2322):

- The server returns `resultType: "input_required"` together with the inputs it needs.
- The client **retries the original call** with the answers attached in `inputResponses`.
- This replaces server-initiated requests that previously required an open stream.

Alongside it, the same spec removes session state from the protocol core: the
`initialize`/`initialized` exchange and the `Mcp-Session-Id` header are gone, each
request carries its own version and capabilities in `_meta`, and `Mcp-Method` /
`Mcp-Name` headers let gateways route without parsing bodies. Tasks moved from
experimental core to a formal extension. Roots, Sampling and Logging are deprecated
with a 12-month window, as is the legacy HTTP+SSE transport.

**Why it appears to fit here:** `/clarification/ask` currently accepts an HTTP POST
from the MCP server and blocks up to 300 s (`ASK_USER_TIMEOUT`, and
`OrchestrationConfig.default_timeout = 300.0`) while a human answers. That is exactly
the shape MRTR replaces — except MACAE built the round-trip by hand, over its own
HTTP bridge, rather than over MCP.

---

## 2. Dependency reality — measured, not assumed

Read from the pinned environment in this repository, not from release notes:

```
$ src/mcp_server/.venv/bin/python -c "import mcp.types as t; print(t.LATEST_PROTOCOL_VERSION)"
2025-11-25
```

| Component | Pinned here | Supports 2026-07-28? |
|---|---|---|
| `mcp` | `1.28.1` (both services) | **No** — latest protocol is `2025-11-25` |
| `fastmcp` | `3.2.0` | No (rides on `mcp` 1.x) |
| `agent-framework` | `1.6.0` | Unknown; requires `mcp>=1.24.0` |
| MRTR support | — | `mcp==2.0.0b1`, **beta**, no stable v2 |

Three consequences:

1. **MRTR cannot be implemented in this repository today.** It needs a beta SDK.
2. **Python v2 renames `FastMCP` to `MCPServer`.** Every service class in
   `src/mcp_server/services/` is registered against `FastMCP`; that is a direct break,
   with a published migration guide.
3. **`agent-framework==1.6.0` is the gating dependency, not `mcp`.** The backend
   consumes MCP through `MCPStreamableHTTPTool`. Until MAF supports `mcp` v2, moving
   `src/mcp_server` to v2 alone would only work because *v2 servers accept legacy
   clients* — real MRTR needs both ends.

The one pin worth changing now is defensive: the MCP maintainers advise library
authors to add `mcp>=1.27,<2` so a stable v2 does not arrive by surprise. This repo
pins `mcp==1.28.1` exactly and `fastmcp==3.2.0` already constrains `mcp <2.0`, so it
is **already safe** — but the constraint is incidental rather than intentional, and
should be made explicit when the pin is next touched.

---

## 3. Track A — polled clarification (IMPLEMENTED)

**This is the part that could start now, and it delivers most of the operational
benefit of MRTR without waiting for anything.**

> **Implemented.** Scope note: what shipped is the *polling* half — the backend
> bridge no longer blocks, and the MCP server polls. Declaring `ask_user` as a
> task-augmented MCP tool was **not** done, because the agent-side client is
> `agent-framework`'s `MCPStreamableHTTPTool` and there is no way to verify from
> this repository that it drives `tasks/get`; declaring the tool a task without
> that support would break it outright. The remaining long-lived hop is
> agent → MCP server, which is internal to the Container Apps environment. The
> hop that was fixed — MCP server → backend — is the one crossing the backend's
> **public** ingress, and therefore the one exposed to idle timeouts.

The Tasks feature already exists in the pinned stack. Verified directly:

```
TASK_STATUS_INPUT_REQUIRED = input_required
Task fields: taskId, status, statusMessage, createdAt, lastUpdatedAt, ttl, pollInterval
FastMCP.tool params: ..., task, timeout, auth
```

`fastmcp==3.2.0` already accepts `task=` and `timeout=` on `@mcp.tool()`, and
`mcp==1.28.1` already defines `tasks/get`, `tasks/result`, `tasks/cancel` and the
`input_required` status. Nothing needs upgrading.

### What shipped

`ask_user` stops being a 300-second blocking HTTP call:

1. `/clarification/ask` registers the clarification, delivers the question over the
   WebSocket, and **returns immediately** with `{request_id, status:
   "input_required", poll_interval_seconds, expires_in_seconds}`. Signed-token
   authorization is unchanged; the token still decides *who* is asked.
2. New `POST /clarification/result` returns `input_required`, `completed`,
   `expired` or `unknown`. It requires the same clarify token **and** checks that
   the token's user owns that request — a valid token for one user is not
   permission to read another's answer. An unrecorded owner is refused, matching
   the approval gate's fail-closed stance.
3. The MCP server creates, then polls at the cadence the backend advertises, until
   an answer arrives or its own wall-clock budget runs out. Each HTTP call now has
   a 30 s timeout instead of 300 s.
4. `OrchestrationConfig` gains `poll_clarification()` and a deadline per request.
   The deadline is **necessary**: with nobody awaiting the event there is no
   `asyncio.wait_for` to expire the entry, so without it the dicts would grow
   without bound. An answered request is deliberately not cleaned up on read, so a
   poll whose response is lost can be retried.

The status vocabulary (`input_required` / `completed`) is deliberately MCP's, so
Track B swaps transport rather than redesigning.

**Deferred:** declaring `ask_user` task-augmented via `@mcp.tool(task=...)`. See
the scope note above — it needs agent-side support that cannot be verified here.

### What it buys

- **No 300-second HTTP request held open.** This is the single largest reliability
  win: today an idle-timeout anywhere in the path — Container Apps ingress, a proxy,
  a load balancer — silently kills a clarification that a human was about to answer.
- **The MCP server becomes retry-safe**, since the answer is fetched by id rather
  than returned on the original connection.
- **It is the same shape as MRTR**, so Track B later becomes a transport swap rather
  than a redesign.

### What it does *not* buy

It does not remove `_clarification_events` from process memory unless the answer
store also moves (see Track C). One replica is still required. **Do not let this
land and be reported as "M3 fixed".**

### Files

- `src/mcp_server/services/ask_user_service.py` — create-then-poll, short per-request timeout
- `src/backend/api/router.py` — `/clarification/ask` returns immediately; new `/clarification/result`
- `src/backend/orchestration/connection_config.py` — key by `taskId`
- `src/tests/backend/api/test_router.py` — three tests asserted the blocking
  contract and were rewritten; nine added for the polled endpoint. That makes 17
  tests in this codebase found asserting behaviour we intended to change.
- `src/tests/backend/orchestration/test_connection_config.py` — eight tests for the
  poll/expiry state machine.
- `src/tests/mcp_server/test_ask_user_service.py` — **new file.** `ask_user` had no
  coverage at all, which is where the riskiest new logic now lives: a loop with a
  deadline and four terminal statuses. Fourteen tests, including that an old
  blocking backend is still honoured during rollout.

---

## 4. Track B — MRTR and the stateless transport (blocked upstream)

**Do not start this yet.** Entry conditions, all three required:

1. `mcp` v2 **stable** released (currently `2.0.0b1`).
2. `agent-framework` publishes a release depending on `mcp` v2 — this is the real
   gate, since the backend reaches MCP through MAF.
3. The `FastMCP` → `MCPServer` migration guide is available for the final API.

### When unblocked, in order

1. Bump `src/mcp_server` to `mcp` v2, rename `FastMCP` → `MCPServer`, update all eight
   service classes in `src/mcp_server/services/` and `core/factory.py`.
2. Convert Track A's task polling into a true MRTR exchange: return
   `resultType: "input_required"`; the client retries with `inputResponses`.
3. Adopt header-based routing (`Mcp-Method`, `Mcp-Name`) — worth doing because the
   MCP server sits on internal ingress behind Container Apps, which can then route and
   rate-limit without parsing bodies.
4. Add `ttlMs` / `cacheScope` to tool and prompt lists. MACAE re-lists tools per agent
   per session across seven content packs; this is a measurable saving.
5. Migrate authorization to CIMD and RFC 9207 issuer validation. **Sequence this after
   C1 is enabled**, not before — doing both at once makes an auth failure ambiguous.
6. Audit for the deprecated Roots / Sampling / Logging features (12-month window) and
   the legacy HTTP+SSE transport.

---

## 5. Track C — the state that actually pins the replicas

Neither MRTR nor Tasks touches this. Three options, in increasing order of effort.

### C1. Externalise the serialisable state only — IMPLEMENTED

Pending approvals and clarifications are written to Cosmos as
`orchestration_request` documents, and waiters read them.

**Behind `ORCHESTRATION_STATE_STORE`, default `memory`.** With the default there
is no Cosmos traffic and no behavioural change whatsoever; the waiter awaits its
`asyncio.Event` exactly as before. Set it to `cosmos` to turn the store on.
Defaulting off is deliberate — this is the approval gate, where a mistake hangs a
plan or releases someone else's.

How the cross-replica case works: the `asyncio.Event` only fires in the process
that recorded the answer, so when the store is on the waiter *races* the event
against a periodic read (`STORE_POLL_INTERVAL_SECONDS`, 2 s). The local event
still wins instantly when the answer lands on the same replica.

Details worth knowing before extending it:

- **`expires_at` is wall clock**, unlike the in-memory deadline, which is
  monotonic. Monotonic is correct within one process and meaningless to another,
  which is the entire point of persisting it.
- **The document id is namespaced by kind** (`approval:` / `clarification:`).
  Approvals are keyed by plan id and clarifications by request id; nothing
  guarantees those spaces never collide, and a collision would let one release
  the other.
- **`session_id` is set to the request id**, making every lookup a
  single-partition point read rather than a cross-partition query per poll.
- **Every operation fails soft.** A Cosmos outage degrades to the previous
  in-memory behaviour rather than propagating; four tests assert this.
- **`plans` was not moved.** It holds `MPlan` objects used for far more than the
  decision gate, and moving it is a larger change with no bearing on whether an
  answer crosses replicas.

- **Unblocks:** approval and clarification answers arriving on any replica.
- **Does not unblock:** the orchestration object and the socket registry. Still one
  replica.
- **Verdict:** necessary, not sufficient.

### C2. Add a socket backplane

`sockets[user_id]` is inherently per-process. Publishing events through Redis pub/sub
or Azure Web PubSub lets any replica deliver to a socket held by another.

- **Unblocks:** streaming progress from a replica that does not hold the socket.
- **Cost:** a new infrastructure dependency, currently absent from both Bicep flavours.

### C3. The orchestration object itself

`orchestrations[user_id]` is a live Magentic workflow and `active_tasks[user_id]` an
`asyncio.Task`. Neither can be serialised. Two credible routes:

- **Session affinity** — accept that a session is pinned to an instance, and make that
  explicit and safe rather than accidental. This is the direction the platform itself
  has taken: Foundry Hosted Agents (MAF 1.0 GA, April 2026) offer per-session VM
  isolation, filesystem state persisted across scale events, and scale-to-zero. That
  is affinity done properly, not statelessness.
- **Durable workflow** — re-express the orchestration so its state is checkpointed and
  resumable. Foundry Agent Service's multi-agent workflows provide a stateful workflow
  layer intended for exactly this. Largest change; the only one that yields genuine
  horizontal scale.

**Recommendation:** C1 now, then evaluate Foundry Hosted Agents before writing any
custom durability layer. The vendor has solved C3 in a supported way, and hand-rolling
checkpointing for a Magentic workflow is a large surface to own.

---

## 6. What not to do

- **Do not adopt `mcp==2.0.0b1` in `main`.** Public APIs may still change between beta
  and stable, and this repository ships as a solution accelerator that others deploy.
- **Do not remove the `maxReplicas: 1` pin** until Track C is done and demonstrated.
  The pin is the only thing preventing a silent hang at the approval gate.
- **Do not do Track B's auth migration and C1 (the front door) in the same change.**
  Two simultaneous auth changes make a failure impossible to attribute.
- **Do not treat Track A as closing M3.** It closes a reliability problem, not the
  scaling one.
- **Do not confuse the two container apps.** The backend and the MCP server have
  separate `scaleSettings` in `infra/avm/main.bicep`. Only the backend carries the M3
  constraint. Raising or auditing the MCP server's `maxReplicas` does nothing for M3 —
  see the correction in §7.
- **Do not assume C1 is active in a deployment.** `ORCHESTRATION_STATE_STORE` defaults
  to `"memory"` (`common/config/app_config.py`). The durable path is opt-in, so the
  Cosmos code can be present, tested and entirely unused.

---

## 7. Verification

Each track needs a different proof, and the last one is the one usually skipped.

| Track | Proof |
|---|---|
| A | Clarification survives longer than the ingress idle timeout — the failure it exists to fix. Assert no HTTP request is held open. |
| B | Protocol version negotiated is `2026-07-28`; a legacy client still works against the v2 server. |
| C | **Run two replicas.** Post an approval to replica A for an orchestration on replica B and watch the plan proceed. Nothing short of this proves it, and no such test exists today — the four `replica` matches under `src/tests/` are unit tests in `test_connection_config.py` and `test_state_store.py`, none of which starts a second process. |

Note this test is the acceptance criterion for **C3**, not C1. C1 makes approvals and
clarifications visible across replicas; `orchestrations`, `sockets` and `active_tasks`
remain per-process, so the test cannot pass on C1 alone.

**An earlier draft of this section said the test was "the one nobody ran before the AVM
flavour shipped `maxReplicas: enableScalability ? 3 : 1`." That was wrong, and the error
mattered enough to correct rather than quietly drop:**

- The **backend** is hard-pinned to `minReplicas: 1 / maxReplicas: 1` at
  `infra/avm/main.bicep:1112`, *regardless of* `enableScalability`, with a comment
  explaining the in-memory state that forbids raising it.
- The `maxReplicas: enableScalability ? 3 : 1` at `infra/avm/main.bicep:1287` belongs to
  the **MCP server** (container `name: 'mcp'`), not the backend.

So the scaling expression was never applied to the stateful component. And after Track A
the MCP server is genuinely safe at three replicas: `ask_user_service.py` holds no task
registry — it POSTs to create, then polls the *backend* for status, so every piece of
clarification state lives backend-side. `stickySessionsAffinity` is additionally set to
`sticky` when `enableScalability` is on.

---

## 8. Sequencing

1. **Now:** Track A, plus make the `mcp<2` bound explicit.
2. **Now, parallel:** Track C1 — externalise serialisable state.
3. **Blocked:** Track B, on `mcp` v2 stable *and* agent-framework support.
4. **Decision needed:** C3 — Foundry Hosted Agents versus a durable workflow. Worth a
   spike before committing.
5. **Last:** remove the replica pin, having run the two-replica test.

---

## Sources

- [The 2026-07-28 Specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/) — stateless core, MRTR (SEP-2322), header routing, deprecations
- [Beta SDKs for the 2026-07-28 spec](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/) — `mcp==2.0.0b1`, `FastMCP` → `MCPServer`, the `mcp>=1.27,<2` advice
- [Microsoft Agent Framework at Build 2026](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-at-build-2026-announce/) — MAF 1.0 GA, Agent Harness, Foundry Hosted Agents
- [Multi-agent workflows in Foundry Agent Service](https://devblogs.microsoft.com/foundry/introducing-multi-agent-workflows-in-foundry-agent-service/) — stateful workflow layer
- Local measurement: `mcp==1.28.1` reports `LATEST_PROTOCOL_VERSION = 2025-11-25`; `fastmcp==3.2.0` exposes `task=`/`timeout=`; `TASK_STATUS_INPUT_REQUIRED` present
