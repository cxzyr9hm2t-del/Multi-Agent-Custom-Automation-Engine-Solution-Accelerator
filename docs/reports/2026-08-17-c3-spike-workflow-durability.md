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

**b. Checkpoints cannot be scoped to a plan, and this is the real blocker.**

An earlier revision of this report said the work was to "thread a stable `thread_id`
through `workflow.run(...)`", on the strength of the `with_checkpointing` docstring:

> Thread ID must be consistent across runs to resume properly.
> `async for msg in workflow.run("task", thread_id=thread_id, stream=True):`

**That docstring is wrong about its own API.** `Workflow.run()` accepts no `thread_id`:

```
run(self, message=None, *, stream=False, responses=None, checkpoint_id=None,
    checkpoint_storage=None, include_status_events=False,
    function_invocation_kwargs=None, client_kwargs=None)
```

Resume is by `checkpoint_id`, found via `get_latest(*, workflow_name)`. So
**`workflow_name` is the partition key for every checkpoint** — and it cannot be set:

- `MagenticBuilder.__init__` exposes **no** name parameter (21 params, none of them a name).
- `MagenticBuilder.build()` takes no arguments.
- `Workflow.__init__` *requires* `name`, so `MagenticBuilder` passes a fixed one.
- Mutating `workflow.name` after `build()` **does not propagate**. `Workflow.__init__`
  constructs its `Runner` with `self.name` (`_workflow.py:350–355`) and the runner copies
  the string (`_runner.py:59`). Measured, not reasoned:

  ```
  name after build:    ORIGINAL
  name after mutation: MUTATED-plan-123
  checkpoints under MUTATED-plan-123: 0
  checkpoints under ORIGINAL:         1
  VERDICT: mutation does NOT propagate — the runner captured the name at build time
  ```

**Why this is a security finding rather than a reliability one.** Every Magentic workflow
built through `MagenticBuilder` therefore shares one `workflow_name`. Today that is
harmless: `orchestration_manager.py:187` constructs a *fresh* `InMemoryCheckpointStorage`
per build, so checkpoints are isolated by process and by object. **Swap in a shared Cosmos
store and that isolation vanishes** — every plan from every user writes under the same
`workflow_name`, and `get_latest(workflow_name=...)` returns whoever checkpointed most
recently. Restoring another user's workflow is strictly worse than the approval-ownership
hole already fixed in `connection_config.py`, and it is the same class of bug: an
identifier assumed to be scoped that is not.

**The fix does not need an upstream change.** `CheckpointStorage` is a Protocol we
implement, so the plan id belongs in the *storage*, not the workflow name — a wrapper that
rewrites `workflow_name` on `save`/`load`/`get_latest`/`list_*`, or a Cosmos partition key
per plan. Since the storage is already constructed per workflow build, that is the natural
seam. Worth also asking upstream for a `name` on `MagenticBuilder`, but nothing waits on it.

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

1. ~~**Scope checkpoints in the storage layer**, since `workflow_name` cannot be set
   (§4b).~~ **DONE** — `orchestration/scoped_checkpoint_storage.py`, wired at
   `orchestration_manager.py:187`. Scoped by `user_id` rather than `plan_id`: no plan
   exists when the workflow is built, and `orchestrations` is keyed by user anyway, so
   `user_id` is both available and the boundary that matters. Wrapping the per-workflow
   in-memory store changes no behaviour today, which is the point — **it had to land
   before any shared store, not after**, or the first Cosmos deployment is a cross-tenant
   read.
2. **Declare `agent-framework-azure-cosmos` explicitly**, pinned, with a comment
   recording that it is beta and why we accepted that.
3. **Swap `InMemoryCheckpointStorage` → `CosmosCheckpointStorage`** behind the same kind
   of opt-in switch C1 used (`ORCHESTRATION_STATE_STORE`), default off.
4. **Solve ownership** before the pin comes off. This is the piece with no vendor answer.
5. **Then the two-replica test** (task #14) — still the only proof, and still the gate.

Step 2 is worth doing regardless of how step 4 is answered — but **with** step 3, not
before it. Pinning a beta distribution the codebase does not yet import buys nothing and
commits us to it early; it belongs in the change that starts using it.

**Step 4 is the real remaining work, and it is larger than this report first implied.**
Nothing in the `CheckpointStorage` protocol prevents two replicas restoring and running
the same workflow. Scoping fixes *whose* checkpoint is read; it says nothing about *how
many* readers act on it. That question has no vendor answer and it gates removing the pin.

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
