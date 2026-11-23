# Standing Order Job — package, debug, acceptance

## Acceptance brief

- A player character's hold-right-click menu offers `Stand Here` when the character is eligible for player orders (selected, non-animal player character).
- Choosing the unshifted action orders the selected eligible characters to the clicked character's current position once.
- Choosing it with Shift adds or updates a visible `Stand Here` job for each selected eligible character. Each job owns one standing position.
- A character with that job walks to its assigned position, then remains there until a higher-priority interruption or a player order replaces/removes the job.
- Combat, incapacity, carrying and other standard task interruptions use the engine's normal task precedence; an eligible character resumes its standing job afterward.
- The job observes the Jobs toggle and can be removed in the normal Jobs panel.
- Assigned positions survive save/load through the mod's save data.
- Job list order matters: an always-eligible `Stand Here` blocks later jobs listed below it (same class of behaviour as Bodyguard). Reassigning updates the job in place so it keeps its index.

## Package

Classification: Meitou C# code mod. FCS data cannot add a context-menu action, retain per-character world positions, or supply custom job behavior.

### Requirements & dependencies

- **[Meitou Client](https://github.com/VlcekM/MeitouClient)**: Meitou code-mod API 2, compatible range pinned in `meitou.json` as `gameVersion` `>=0.22.2 <0.23.0`.

Job type number `1000` (`SimulationExtensions.FirstModCode`), named `Stand Here`.

Intended load order:

```text
StandingOrderJob.mod
```

Build and stage (disposable symlink + `out/staging/mods.cfg` only; never edits the player's live `data/mods.cfg`):

```bash
export MEITOU_CLIENT_ROOT=/path/to/MeitouClient
export KENSHI_PATH=/path/to/Kenshi
./tools/package.sh
./tools/stage.sh
```

Package folder `out/StandingOrderJob/` contains `StandingOrderJob.mod`, `meitou.json`, and `StandingOrderJob.dll`.

## Debug

Set `MEITOU_STANDING_ORDER_JOB_DEBUG=1` before launching Meitou. The mod writes transition lines to Meitou's `log.txt` prefixed with `mod       standing-order-job:` (command receipt, assign, walk/hold/stop, prune). It never posts player-facing diagnostic messages.
