# C3 spike — the workflow object is already checkpointed, into the wrong place

Result of task #17, run against the pinned environment on 17 August 2026. Measured, not
inferred — every claim below came from `src/backend/.venv` at `agent-framework==1.6.0`,
not from the Build 2026 announcement.

**Headline: the C3 fork in [the MRTR note](2026-08-16-mrtr-migration-path.md) is a false
dichotomy.** It offered Foundry Hosted Agents versus a hand-rolled durable workflow.
There is a third option, it is cheaper than both, and most of it is already in the tree.

---

## 1. We are already checkpointing the workflow

`orchestration/orchestration_manager.py:187–193`:

```python
storage = InMemoryCheckpointStorage()
workflow = MagenticBuilder(
    ...
    checkpoint_storage=storage,
    ...
).build()
```

`MagenticBuilder` has taken `checkpoint_storage` all along and we have been passing it.
The Magentic workflow **is** being serialised at every significant state transition
today — into process memory, which is precisely the one place that is useless across
replicas.

`with_checkpointing`'s own docstring:

> Checkpointing allows workflows to be paused, resumed across process restarts, or
> recovered after failures. The entire workflow state including conversation history,
> task ledgers, and progress is persisted at key points.

## 2. `CheckpointStorage` is a Protocol, and Microsoft already implemented it for Cosmos

`agent_framework/_workflows/_checkpoint.py:119` — `class CheckpointStorage(Protocol)`,
six methods, structural typing so no inheritance is required:

| Method | Signature |
|---|---|
| `save` | `(checkpoint) -> CheckpointID` |
| `load` | `(checkpoint_id) -> WorkflowCheckpoint` |
| `delete` | `(checkpoint_id) -> bool` |
| `get_latest` | `(*, workflow_name) -> WorkflowCheckpoint \| None` |
| `list_checkpoints` | `(*, workflow_name) -> list[WorkflowCheckpoint]` |
| `list_checkpoint_ids` | `(*, workflow_name) -> list[CheckpointID]` |

And `agent_framework_azure_cosmos.CosmosCheckpointStorage` is **already installed**:

> Implements the ``CheckpointStorage`` protocol using Azure Cosmos DB NoSQL

```
CosmosCheckpointStorage(*, endpoint=None, database_name=None, container_name=None,
                        credential=None, cosmos_client=None, container_client=None,
                        env_file_path=None, env_file_encoding=None,
                        allowed_checkpoint_types=None)
```

We do not implement this. We do not hand-roll durability. We do not migrate to Foundry
Hosted Agents. **The candidate change is swapping the storage backend at one call site.**

## 3. This corrects the load-bearing row of the MRTR note

The note's state table marks `orchestrations[user_id]` — the live Magentic workflow — as
**Serialisable? No**, and that "No" is the reason the whole document concluded C3 was hard.
It is wrong. The framework serialises it. `WorkflowCheckpoint` carries:

`workflow_name` · `graph_signature_hash` · `checkpoint_id` · `previous_checkpoint_id` ·
`timestamp` · `messages` · `state` · `pending_request_info_events` · `iteration_count` ·
`metadata` · `version`

Two of those are worth dwelling on. `graph_signature_hash` means the framework refuses to
restore a checkpoint into an incompatible graph — the corruption guard you would otherwise
have to invent. And `pending_request_info_events` suggests the pending plan-review request
is *inside the checkpoint*, which means C1 and C3 overlap rather than stack.

---

## 4. What this does **not** fix — read before anyone calls M3 closed

**a. It is beta, and we do not declare it.** `agent-framework-azure-cosmos` is
`1.0.0b260521`, and it reaches us **transitively** via `agent-framework-core[all]`
(`uv.lock:214`), not from `src/backend/pyproject.toml`. §6 of the MRTR note says do not
adopt `mcp==2.0.0b1` in `main` because APIs may shift before stable and this repository
ships as a solution accelerator others deploy. **That rule applies here unchanged.**
Depending on a beta API is a decision; depending on one *transitively*, where an upstream
change to the `all` extra could remove it silently, is not a decision — it is an accident
waiting to happen. If adopted it must be pinned explicitly.

**b. There is no `thread_id` in `orchestration_manager.py`.** The docstring is explicit:
*"Thread ID must be consistent across runs to resume properly."* Swapping storage without
threading a stable id (`plan_id` is the natural candidate) through `workflow.run(...)`
buys durable writes that can never be resumed. **This, not the storage swap, is the
actual work.**

**c. Two rows of the state table are untouched.** `sockets[user_id]` is inherently
per-process — that is still C2, a socket backplane. `active_tasks[user_id]` is an
`asyncio.Task` and cannot be checkpointed.

**d. A new risk this introduces.** Once a checkpoint is restorable by any replica,
nothing in the `CheckpointStorage` protocol stops *two* replicas restoring and running
the same workflow concurrently. Today the `maxReplicas: 1` pin makes that unthinkable;
removing the pin makes it the default failure mode. Ownership — a lease, a leader
election, or a Cosmos conditional write — is now part of C3's scope and was not in the
original framing.

---

## 5. Recommendation

Foundry Hosted Agents is no longer the cheapest path and the spike does not recommend it.
Sequence instead:

1. **Thread a stable `thread_id`** (`plan_id`) through the workflow run. No new
   dependency, testable on one replica, and useless work is impossible — resume needs it
   under every option.
2. **Declare `agent-framework-azure-cosmos` explicitly**, pinned, with a comment
   recording that it is beta and why we accepted that.
3. **Swap `InMemoryCheckpointStorage` → `CosmosCheckpointStorage`** behind the same kind
   of opt-in switch C1 used (`ORCHESTRATION_STATE_STORE`), default off.
4. **Solve ownership** before the pin comes off. This is the piece with no vendor answer.
5. **Then the two-replica test** (task #14) — still the only proof, and still the gate.

Steps 1 and 2 are worth doing regardless of how step 4 is answered.

---

## Sources

- Local measurement in `src/backend/.venv`, `agent-framework==1.6.0` /
  `agent-framework-core==1.6.0` / `agent-framework-foundry==1.6.0`,
  `agent-framework-azure-cosmos==1.0.0b260521`
- `agent_framework/_workflows/_checkpoint.py:119` — `CheckpointStorage(Protocol)`
- `agent_framework_azure_cosmos/_checkpoint_storage.py:35` — `CosmosCheckpointStorage`
- `agent_framework_orchestrations/_magentic.py:1419,1564` — `checkpoint_storage`,
  `with_checkpointing`
- `src/backend/orchestration/orchestration_manager.py:187–193` — our call site
- `src/backend/uv.lock:113,214` — version and transitive provenance
