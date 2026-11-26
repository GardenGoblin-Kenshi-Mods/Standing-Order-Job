# Standing Order Job — behavioral specification

This document is the authoritative **BDD North Star** for `Standing Order Job`: observable behaviors drive implementation, verification gates, and higher-level `README.md` features.

## Overview & scope

`Standing Order Job` adds a `Stand Here` action to a selected player character's hold-right-click menu, with Shift assigning a visible permanent `Stand Here` job (`TaskType 1000`) tied to one standing coordinate per character and a configurable maximum return distance (`apiVersion: 3`, assembly `StandingOrderJob`).

- **Explicit non-goals (v0.1):** No formation spread/offset when multiple squad members are selected (every recipient receives the exact clicked character's snapshot coordinate), and no overriding of Shift ground-click travel.

---

## Behavioral specification

### `B-01` Character-menu verb gating

- **Given** a player holds right-click on a target character in the world,
- **When** the character menu opens (`StandHereUi.Verbs`),
- **Then** the **`Stand Here`** row is shown **only if** `Target.IsPlayer == true`, `Target.Animal == false`, and `Selected.Contains(Target.Id)`.
- **Guards & edge cases:** Clicking a player character outside the current selection, or clicking an animal or non-player character, omits the `Stand Here` verb.

### `B-02` Unshifted `Stand Here` (one-shot move)

- **Given** one or more characters are selected and the player chooses `Stand Here` without holding Shift,
- **When** `StandHereSystem.Apply` runs in serial `Inputs`,
- **Then** valid finite coordinates (`StandHereTypes.IsFinitePoint(point)`) defer a vanilla `MoveOrder(recipients, point) { Tick = world.Tick + 1 }` via `world.Defer` for every selected living, non-animal player character, leaving any existing `Stand Here` job and saved standing points unchanged.

### `B-03` Shifted `Stand Here` (permanent job assignment)

- **Given** one or more characters are selected and the player holds `Shift` while choosing `Stand Here`,
- **When** `StandHereSystem.Apply` validates finite coordinates and eligible living non-animal player recipients,
- **Then** for each eligible recipient:
  - Acquires a persistent `LastingId` (`world.LastingIds.GetOrGive`) and stores/overwrites `_points[lasting] = point` (the clicked target's snapshot position; no formation offset).
  - Updates an existing `PermaJob(1000)` in-place via `PermaJobs.IndexOf` (preserving job-list priority) or appends `new PermaJob(1000, null, Hand.Null)` if absent.
  - Enables `CharacterOrders.Jobs` and calls `DecisionSystem.Request`.
- **Guards & edge cases:** Living incapacitated player characters **can** receive/update the job and point in `Apply` (only dead, animal, or non-player targets are skipped), while execution waits until they are conscious (`B-04`).

### `B-04` Standing & arrival hold without repath spam

- **Given** a character has an active `Stand Here` job (`Jobs` enabled, conscious, non-animal player, stored `_points[lasting]`),
- **When** `StandHereJobs.CanRun` and `Start` evaluate (`NearRadius = 1.5f` horizontal 2D metres, ignoring Y),
- **Then**
  - Outside `1.5m` horizontal radius: checks `DestinationAccess.WalkRefused(world, slot, 1000, point)` and, if passable, walks via `TaskRequest(CharacterTask.GoTo, 1000, TaskSource.Job) { Job = k, Point = point, Mode = cold.RunSpeed }`.
  - Inside `1.5m` horizontal radius: starts `TaskRequest(CharacterTask.Idle, 1000, TaskSource.Job) { Job = k }`; on subsequent ticks, `CanRun` checks `IsNearPoint` before `run.Runs(task)` and returns `DecisionCheck.Runs` so arrival switching the hot task from `GoTo` to `Idle` never causes repath spam.
- **Guards & edge cases:** Incapacitated characters return `No("ineligible")` in `CanRun` until conscious, then resume walking to their saved point.

### `B-05` Interruptions & job-list priority

- **Given** a character with a `Stand Here` job encounters combat, carrying, medical incapacity, a higher-priority job above `Stand Here`, or a manual player move/order,
- **When** higher-priority tasks or player orders preempt the job (`Stop` runs `TaskStart.Stand` if hot task is `GoTo`),
- **Then** `_points[lasting]` is preserved so the character returns to its assigned spot once free (if within return range, `B-08`). While eligible and within return range, `Stand Here` holds continuously so jobs listed *below* it in the Jobs panel are blocked; when out of range (`B-08`), `Stand Here` skips cleanly so lower jobs can run.

### `B-06` Job removal & ghost-walk cancellation

- **Given** a character loses their `Stand Here` job row from the Jobs panel (`PermaJobs.IndexOf(cold, 1000) < 0`),
- **When** `StandHereSystem.PruneAndCancel` runs in `Inputs`,
- **Then** `_points` removes `lasting` and `CancelGhostWalk` cancels any lingering `{ Source: TaskSource.Job, Row: 1000 }` running walk (`TaskStart.Stand` on hot `GoTo` and clears `cold.Running`).

### `B-07` Unloaded zones, persistence & determinism

- **Given** characters move into unloaded zones or across save/load and multi-threaded runs,
- **When** `PruneAndCancel`, `IModSaveData` (`SaveFormat = 1`), or `IStateHashed` execute,
- **Then**
  - Unloaded characters (`world.LastingIds.Find(lasting).IsNone`) **keep** their `_points[lasting]` entry (only genuinely missing slots or removed jobs prune points).
  - Save/load writes and reads `SaveFormat = 1` entries sorted by `LastingId.Value` (`!id.IsNone && StandHereTypes.IsFinitePoint(point)`).
  - `IStateHashed.Hash` hashes `_maxReturnDistanceMeters` and sorted `_points` entries deterministically.

### `B-08` Maximum return distance & Mods-page setting

- **Given** a character has an active `Stand Here` job and stored stand point, and **Mods → Standing Order Job** configures `Max return distance (m)` (`0..2000`, default `652 m` matching Meitou's vanilla `ProductionSystem.JobRangeSq` ~651.7 m, step `1`; `0 = unlimited`),
- **When** `StandHereJobs.CanRun` / `Start` evaluate the character's 3D squared distance against `(maxReturnDistanceMeters * 10)^2` world units (Meitou uses decimetres per metre), or when the Mods-page slider changes mid-session,
- **Then**
  - When `maxReturnDistanceMeters > 0` and 3D distance `>= maxReturnDistanceMeters * 10` world units, `CanRun` returns `DecisionCheck.No("out of range")` and `Start` guards without walking; if the character was walking back, `DecisionSystem` invokes `Stop`, allowing lower-priority jobs or idling to run while preserving `CharacterOrders.Jobs`, the job row, and `_points[lasting]`.
  - Returning within range, increasing `Max return distance (m)`, or setting it to `0` (`unlimited`) resumes `Stand Here` automatically.
  - Changes on the Mods page drain thread-safely through `SettingsBridge` at the start of serial `Inputs` and call `DecisionSystem.Request` in deterministic `LastingId.Value` order for loaded assigned characters.

---

## Settings & defaults

Registered in `StandHereSettings` via `context.AddSettings` (persisted by Meitou in `meitou.user.json`; `SaveFormat = 1` save payload unchanged):

| Setting | Subgroup | Type & Range | Default | Behavior |
| --- | --- | --- | --- | --- |
| `Max return distance (m)` | `Return distance` | `int`, `0..2000`, step `1` | `652` | Maximum 3D distance (metres) a character walks back to hold their point; `652` matches Meitou's vanilla job range (`ProductionSystem.JobRangeSq`). `0` disables the distance cap. |

---

## Verification matrix

| Behavior ID | Unit / Headless Gate (`tools/test.sh`) | Data / Static Gate | Determinism Gate | Playtest / Save Gate |
| --- | --- | --- | --- | --- |
| `B-01` | — | `--data-check` (`StandingOrderJob.mod`) | `meitou-tools mod-check` | Menu row visibility: selected player, unselected player, animal |
| `B-02` | `UnshiftedStandHereMovesCharacterWithoutAlteringExistingJobOrPoint` | — | `mod-check` | Unshifted `Stand Here` moves selection once without altering existing job/point |
| `B-03`, `B-04` | `ShiftedStandHereFiltersMixedRecipientsUpdatesInPlaceAndWaitsUntilConscious`, `CharacterWalksToStandPointAndHoldsIdleWithoutRepathSpam` | — | `mod-check` | `Shift+Stand Here` multi-select assign/reassign in-place, walk to spot, hold `Idle` without repath spam |
| `B-05`, `B-06` | `OutOfRangeSkips...`, `RemovingStandHereJobPrunesPointAndCancelsLingeringWalk` | — | `mod-check` | Interrupt/resume across combat & incapacity, list priority vs Medic above/below, job removal cancels ghost walk |
| `B-07` | `SaveReloadRoundTripRestoresStandPointAndResumesHolding`, `UnloadedCharacterPointSurvives...`, `ReadRejectsInvalidFormats...`, `StateHash...`, `RepeatedHeadlessRuns...` | — | `mod-check` | Unloaded-zone retention, save/load round-trip (`SaveFormat = 1`), `MEITOU_STANDING_ORDER_JOB_DEBUG=1` trace check |
| `B-08` | `StandHereTypesTests`, `SettingsBridgeTests`, `OutOfRangeSkipsWithoutDisablingJobsLetsLowerJobRunAndResumesWhenRangeIncreasesOrZeroUnlimited` | — | `mod-check` | Character beyond `Max return distance (m)` skips `Stand Here` without disabling `Jobs`, resumes on return; `0` allows map-wide return |

---

## Mod-page feature derivation

| Behavior IDs | Distilled `README.md` Feature |
| --- | --- |
| `B-01` | Context-menu `Stand Here` verb for selected, non-animal player characters. |
| `B-02` | Unshifted one-shot move via standard `MoveOrder`. |
| `B-03` | `Shift+Stand Here` assigns or updates a permanent `Stand Here` job in-place with one standing point per character. |
| `B-04`, `B-05` | Walks to point and holds steady; yields to combat, incapacity, carrying, and higher-priority jobs, then resumes afterward. |
| `B-08` | Configurable maximum return distance in `Mods -> Standing Order Job` (default `652m` matching vanilla jobs; `0` = unlimited); skips out-of-range posts without disabling Jobs or erasing points. |
| `B-05`, `B-06` | Respects the Jobs toggle, preserves priority order on reassignment, and cleans up on removal from the Jobs panel. |
| `B-07` | Assigned standing positions survive zone unloads and save/load. |
