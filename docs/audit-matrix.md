# Existing-mod audit matrix (Factory pilot)

Snapshot of `g4rd3ng0bl1n/Kenshi-Mods` mods at template `v0.1.0` design time. Baseline for shared Copier scaffolding.

## Inventory

| Mod slug | Kind | Notable extras |
| --- | --- | --- |
| `butchering` | code | Jobs/UI/settings; `DebugLog` |
| `standing-order-job` | code | Smallest pure code mod (**template baseline**) |
| `playable-hivers` | data + code | Birth UI, assets helpers |
| `kenshi-kids` | code + compat | `compat/`, `RE_Kenshi.json`, growth/scale patches |
| `playable-hivers-kenshi-kids-bridge` | code bridge | `dependencies` / `loadAfter`; `stage-combined.sh` |

## Shared conventions (in template)

| Area | Convention |
| --- | --- |
| Layout | `meitou.json`, `README.md`, `docs/`, `src/`, `tools/`, ignored `out/` |
| Target | `net11.0`, `MeitouClientRoot` ProjectReference to `Meitou.Game` |
| Package | `tools/package.sh` builds DLL + empty FCS `.mod` + copies `meitou.json` |
| Stage | `tools/stage.sh` symlinks package into `$KENSHI_PATH/mods`, writes disposable `out/staging/mods.cfg` |
| Manifest | `apiVersion` 2; `assemblies` + `entry`; optional `gameVersion` |
| Debug | Env-gated `DebugLog` → `ModContext.Log`, never player-facing diagnostics |
| Docs | README acceptance brief + package/stage instructions |

## Intentionally not in the default template

| Feature | Why optional / excluded |
| --- | --- |
| `dependencies` / `loadAfter` | Bridge-only; add when needed |
| `stage-combined.sh` | Multi-mod staging; bridge-specific |
| `compat/` / `RE_Kenshi.json` | Port-specific; not Meitou-native default |
| Harmony patches / settings UI | Mod gameplay, not scaffolding |
| FCS data trees / assets | Separate data/asset template work |
| Steam / Nexus publish workflows | Factory issues #4 / #5 |

## Recommendations

1. Keep Copier answers limited to identity, docs toggles, and CI toggle.
2. Prefer central reusable GitHub Actions later; ship a thin layout-check/build stub now.
3. Do not force bridge or asset mods into this skeleton without optional flags in a later template version.
