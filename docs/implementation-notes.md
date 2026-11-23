# Implementation notes

## Scope

Implement `Standing Order Job` as a Meitou API 2 code mod. It adds a player-facing `Stand Here` action to a player character's hold-right-click menu. Shift assigns the action as a permanent `Stand Here` job, matching existing Shift job actions rather than replacing Shift-ground movement.

The assembly targets Meitou `>=0.22.2 <0.23.0`, pinned in `meitou.json`.

The standing point is the clicked character's published snapshot position. Selecting several characters assigns each of them that same point; placement spread is intentionally not part of this first version.

## Runtime design

1. Claim job type `1000` via `ModContext.AddJobType` as `"Stand Here"`.
2. Register `StandHereSystem` after `StandardParts.Player`. The system implements `IChooserOwner`, `IModSaveData`, and `IStateHashed`. `Register` calls `ChooserRegistry.Jobs`.
3. Register a character-menu verb. Its action queues `StandHereCommand` and reads `UiMenuContext.Shift` when activated. Animals-only selections drop the row in Meitou's menu filter; mixed selections still filter animals in Inputs.
4. In the system's serial input phase, validate recipients and the point. For Shift, update-in-place or append a `PermaJob` of type `1000` (never `PermaJobs.Add`), store the point by lasting id from `GetOrGive` in Inputs only, and turn the jobs switch on. Without Shift, `world.Defer(new MoveOrder(...) { Tick = world.Tick + 1 })`.
5. `IJobSource`: near the stored point return `DecisionCheck.Runs` (walk arrival leaves `Running.Task` as `GoTo` while hot task is `Idle`). Away from the point, `Start` walks with `TaskSource.Job` and `Job = k`. Missing point → `No`. Prune points and cancel ghost walks when the job is removed from the list.
6. Persist lasting-id → `Vector3` through `IModSaveData`; hash the same map with `IStateHashed`.
7. Opt-in `MEITOU_STANDING_ORDER_JOB_DEBUG=1` via `ModContext.Log`.

## Behavior decisions

- Shift is read by the menu row itself; it does not change the ground-click travel gesture.
- Reassigning a `Stand Here` job replaces that character's previous standing point and updates the existing list entry in place.
- An always-eligible stand job blocks later jobs listed after it; player list order is the control.
- A temporary interruption such as combat or incapacity does not discard an existing job or its saved point.
- No formation offset in the first version.

## Package layout

```text
mods/standing-order-job/
  README.md
  docs/implementation-notes.md
  meitou.json
  src/
  tools/
  out/                 # ignored
```

Package contents: `StandingOrderJob.mod`, `meitou.json`, `StandingOrderJob.dll`.

## Validation plan

1. Build the assembly and run `./tools/package.sh`.
2. Stage with `./tools/stage.sh`; verify disposable `mods.cfg` contains only `StandingOrderJob.mod`; run Meitou `--data-check`.
3. Run `meitou-tools mod-check` on a disposable save/package when available.
4. Playtest: menu row; Shift multi-select; hold without repath spam; unshifted move; interrupt/resume; Jobs toggle/remove; list-order vs Medic; save/load; animal filter.
5. With debug env set, confirm transition lines in `log.txt`.

No automated test case has been added: the factory requires explicit approval before adding tests.
