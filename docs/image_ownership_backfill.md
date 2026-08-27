# Closing the legacy-image fallback

Audit finding **M7**. Generated images are checked against a recorded owner, but
images generated before that recording existed have no record. Those fall back
to token-only protection: the requester must hold a valid signed token, but not
necessarily be the person the image belongs to.

This is the sequence that ends the fallback. Do it in this order — enabling
enforcement before the backfill has run stops every pre-record image rendering.

---

## 1. See what is missing, without changing anything

```bash
PYTHONPATH=src:src/backend python scripts/backfill_image_ownership.py --dry-run
```

Needs the same Cosmos configuration and `az login` as running the backend.

It reads every stored agent message across all users, finds the generated-image
URLs in each, and reports which of those images have no owner recorded. Nothing
is written.

Read the summary line before going further:

```
N image(s) referenced; A already have an owner, B to record,
C skipped for having no user on the message.
```

- **B** is what the next step will record.
- **C** is images it cannot attribute, because the message carrying them has no
  user on it. These stay unowned, and enforcement will deny them. If C is not
  zero, decide whether that is acceptable before step 3 — the images are
  unreachable afterwards, not deleted.

If the script reports that no messages were returned, it exits non-zero rather
than claiming a clean run: an empty read and a failed query look identical from
inside, and reporting success on a failed read is how you end up enforcing
against a database that was never backfilled.

## 2. Record the owners

```bash
PYTHONPATH=src:src/backend python scripts/backfill_image_ownership.py
```

Safe to re-run. Ownership is first-writer-wins, so an existing record is never
overwritten and an image cannot change hands by being echoed in someone else's
message. Nothing is deleted and no message is modified.

The script derives each owner by calling `record_image_ownership` — the same
function the live path calls when an image first appears in a user's agent
output — rather than reimplementing the rule, so the two cannot drift apart.

It re-reads each image after writing and reports `Recorded X of Y`. If those
disagree it exits non-zero. Do not continue to step 3 until a run reports a
clean result.

## 3. Turn on enforcement

Set on the backend container app:

```
IMAGE_REQUIRE_OWNERSHIP_RECORD=true
```

An image with no ownership record is now denied with a 403 instead of being
served on its token alone. Images whose owner matches the requester are
unaffected.

To roll back, unset it or set it to anything other than `true` / `1` / `yes`.
The fallback returns immediately; no data changes either way.

## 4. Remove the fallback branch

Once enforcement has been on in production long enough that you are confident
nothing legitimate was denied, the `IMAGE_REQUIRE_OWNERSHIP_RECORD is false`
branch in `get_generated_image` (`src/backend/api/router.py`) can be deleted
along with the flag, making the strict behaviour unconditional. That is a code
change, not a configuration one, and is the point at which M7 is fully closed
rather than bounded.

---

## What this does not fix

The ownership check answers "is this image the requester's?" It rests on the
requester's identity being trustworthy, and by default it is not: the backend
takes identity from a header the caller supplies, because the Container Apps
auth front door is off (`backendAuthClientId` empty, `ingressExternal: true`).
That is audit finding **C1**, and until it is enabled this makes cross-user
image access harder to do by accident, not impossible to do on purpose. See
[`backend_api_authentication.md`](backend_api_authentication.md).
