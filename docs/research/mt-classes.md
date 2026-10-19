# MT Framework class registry (DTI)

Recovered with `tools/mt_dti.py` from USA v1.3.0. Full table: [`config/mt_classes.tsv`](../../config/mt_classes.tsv) (DTI object address, name, parent, instance size, class id).

## Result
- **992 classes**, every parent resolved, single root **`MtObject`** (size 4: just a vtable) plus the separate `MtGeomConvex` tree.
- 990 recovered by emulating the static initializers, 2 by static register tracking.

| Family | Count | What it is |
|---|---|---|
| `u…` units | 371 (343 under `cUnit`) | Things that live in the world and update per frame: models, effects, lights, filters, layout/UI, **55 monster classes** (`uEm001`…`uEm118`, all under `uEnemy`) |
| `n…` namespaces | 173 | Subsystems: `nNetwork`, `nDraw`, `nGUI`, `nCollision`, `nSwkbd` (software keyboard), `nCec` (StreetPass), `nApplet` |
| `s…` systems | 120 | Singletons/managers and their inner types: `sCollision`, `sPad`, `sResource`, `sUnit`, `sCamera`, `sSaveData` |
| `r…` resources | 117 (93 under `cResource`) | **One class per loadable file type**; see the list below |
| `c…` | 102 | Engine/game base classes: `cUnit`, `cResource`, `cArea`, `cSystem`, `cDraw`, `cQuestBoard` |
| `Mt…` | 71 | Framework core: allocators, containers, streams, files, threads, geometry |
| `a…` areas | 33 (11 under `cArea`) | Top-level game modes: `aInit`, `aVillage`, `aQuest`, `aArena`, `aDemo`, `aNewGame`, `aContinue`, `aEvent`, `aGameSys`, `aDLContents`, `aViewer` |

## How it works
- Constructor: **`0x005BFA94`**, called 993 times from static initializers:
  `MtDTI(this, name, parent, size, arg4, flags, id)`, with the last three on the stack.
- Fields it writes: `+0x00` vtable, `+0x04` name, `+0x14` cleared, `+0x18` size (low 23 bits) with flags in the top bits, `+0x1C` id.
- After the call, each initializer overwrites `+0x00` with the class-specific DTI vtable (it holds the class's "create instance" hook). **Next:** read those vtables to find each class's constructor and its main vtable.
- **Class id:** `~crc32(name) & 0x7FFFFFFF` (CRC-32 without the final inversion, 31 bits) for 989/992 classes; the other 3 pass an explicit id. `mt_dti.mt_hash()` implements it.

## Resource classes (format research checklist)
Archives (`rArchive`) identify each entry's type by this class id, so the table above maps every archive entry to its resource class. Each line below is a file format to document:

rArchive, rCameraList, rChain, rChainCol, rCnsIK, rCnsJointOffset, rCnsMatrix, rCnsTinyChain, rCnsTinyIK, rCollision, rCollisionHeightField, rCollisionObj, rConstraint, rConvexHull, rDynamicSbc, rECPackData, rECPackLevelUpTbl, rEffect2D, rEffectAnim, rEffectList, rEffectStrip, rEmSetData, rEnemyAtari, rEnemyAtkHitData, rEnemyCmd, rEnemyData, rEnemyDtBase, rEnemyDtBaseBui, rEnemyDtBaseEtc, rEnemyDtBaseMap, rEnemyDtBasePos, rEnemyDtTune, rEnemyTuneData, rEventActorTbl, rGUI, rGUIFont, rGUIIconInfo, rGUIMessage, rGeometry2, rGeometry2Group, rGeometry3, rGrass, rGrass2, rGrass2Setting, rGrassWind, rItemPopList, rItemPopSet, rLayout, rLayoutAnime, rLayoutAnimeList, rLayoutFont, rLayoutMessage, rLtProceduralTexture, rLtShader, rLtSoundBank, rLtSoundCategoryFilter, rLtSoundRequest, rLtSoundReverb, rLtSoundSource, rLtSoundSourceADPCM, rLtSoundStreamRequest, rMHSoundEmitter, rMHSoundSequence, rMaterial, rMhMotionEffect, rModel, rMotion, rMotionList, rMovie, rMovieOnDisk, rMovieOnMemory, rNulls, rQuestData, rQuestInfoTbl, rRenderTargetTexture, rScheduler, rSoundAttributeSe, rSoundCurveSet, rSoundDirectionalSet, rSoundMotionSe, rSoundSequence, rSoundSequenceIntermediate, rSoundSequenceSe, rSoundSimpleCurve, rStageAreaInfo, rStageCameraData, rStageInfoSet, rSwkbdMessageStyleTable, rSwkbdMessageTable, rSwkbdSubGroup, rTexture, rVertices, rVibration

Suggested priority for the port: `rArchive` → `rTexture`, `rModel`, `rMaterial`, `rMotion` (get something on screen) → `rGUI*`/`rLayout*` (menus) → quest/enemy data tables.
