# PC MOBA Towers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. User explicitly chose direct implementation; no repeated approval pause.

**Goal:** 20-second 3+2 waves, four high-damage towers able to kill/respawn the player in desktop MOBA, with preserved histories.
**Architecture:** Register immutable pc-moba-v5 beside v4. Extend pure lane simulation with optional signed v5 rules, render procedural towers and player HUD in existing Three/React Host.
**Tech Stack:** TypeScript/React/Three/Vitest/Playwright; FastAPI/Pydantic/PostgreSQL/Pytest; existing NAS script.
**Spec:** docs/superpowers/specs/2026-11-10-pc-moba-towers-design.md (read fully for exact values).

## Global Constraints

- Actual source `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`; external detached dirty worktree. Preserve existing changes; noGit stage/commit/reset/clean/push. Baseline-relative review, notHEADdiff.
- v5:300000ms,20s/15waves,eachside3melee+2ranged/75;4towers;hero1000HP/5000msrespawn;exacttowerstats/positions in spec. v4 unchanged50/30s/noherohealth/towerfields. Allother suites unchanged.
- New independent params/raw kind `lane_last_hit_towers`; do not weaken v4 serialization, validation, recovery or ranking. No DB migration/permission expansion.
- TDD real behavior and independent expectations; no raw injection into native tests, no fakeclock for actual300s gate, no quality-gate bypass.
- Workers not alone: exclusive ownership below, no subagents; root coordinates reviews and DB leases. DB55443Task1only, DB55444rootnative; Python cwdapps/api.
- Python tools `/Volumes/MacDev/Projects/new project3/.venv/bin`; existing node_modules. No dependencies added. Root runs fullfrontend only aftersourcefreeze; workerfocusedchecks notfullsuitewhileothersedit.
- No production data writes. StandardNAS script withNAS_SSH_PORT202 mandatory afterchecks; failuresblockrelease.

## Review Focus

- Old v4 serialized config must remain identical, preserving ranking eligibility and pending results (Task1 frozen fixture comparison).
- A tower projectile from an old hero life cannot hit the respawned hero, and dying does not replay input (Task2 lifecycle tests).
- Four towers cannot turn allied kills into player score or kill a unit twice in same tick (Task2 attribution +Task1 boundedraw).
- New v5 cannot be misclassified as old four-item60s by UI or seed/testhelpers (Task3 integrated routing/recovery).
- Actual tower bodies can occlude targets without selecting an invisible enemy; their animation does not resizecanvas or obscureHUD (Task4 Three raycast +Task3 realbrowser).

### Task 1: Frozen v5 contracts, persistence and scoring

**Ownership:** apps/api/app/esports_assessments/{config_schemas,raw_schemas,rule_versions,catalog,formal_service,practice_service,reporting,scoring,ranking,ranking_schemas,schemas}.py; app/audit/service.py ifnewallowlistrequired; corresponding tests/unit,services,api/test_esports*.py and tests/esports_helpers.py; apps/web/src/esportsAssessments/{contracts.ts,versionRules.ts,contracts*.test.ts,testSupport/fixturesMobaV5.ts,testSupport/catalog-moba-v5.json}. NoCLI/UI/runtimeedits.

**Interfaces:** Exports `LaneTowerLastHitParams`, `LaneTowerLastHitRaw`, `isLaneTaskConfig(config:TaskConfig): boolean/typeguard`, `fixtureMobaV5Config(attack_mode:'melee'|'ranged'='melee'):TaskConfig`; exact shape/spec. Publish TS signatures early, before downstream edits consume them. Backendnewheadcurrentv5 retains explicitv4. Existingv4 fixtures immutable.

- [ ] RED new tests `test_esports_moba_v5.py` unit/service/API and `contractsMobaV5.test.ts`: new75/20s/fullfields;v4dumpmatchesoldfixture;v4rejectsID50/20ssecondwave;v5accepts74born280000;reject300000;2/75rounding=3;50/75=67;75/75=100;death/damageconsistency;mode/poolisolation/legacyreplay.
- [ ] RunfocusedRED; recordassertionfailures beforeimplementation.
- [ ] Register separatev5schemas/catalog/report identity and mode requirement. Addstrictlife stats; scorefromfrozencount. Updateonlycurrent-defaultfixtures toexplicitoldversionwherehistorical, preserveexpectations. `isLaneTaskConfig` guardsbothregisteredversions/kinds, neverarbitrarykind.
- [ ] GREEN allrelatedbackend tests serial55443 (`tests/unit/test_esports* tests/services/test_esports* tests/api/test_esports*`) +contracts tests+Ruff+mypy; recordexactresultsandfailuresresolved. Nootherresetprocess.
- [ ] Selfreview, writeTask1report/filelist; rootpackagesincrementagainstbaseline anddispatchesreview. NoGitcommit.

### Task 2: Pure tower combat and hero lifecycle

**Ownership:** apps/web/src/esportsAssessments/lane/{constants,simulation,combat,types,engine}.ts and correspondingtests; suites/pcMoba.ts/test. May addsmallpuretowerhelper underlane. No renderer/models/HUD/Host/contracts edits.

**Interfaces:** ConsumeTask1types. `LaneSimulation(mode,seed,params?:LaneTowerLastHitParams)` defaultv4 unchanged; rawreturnunion. Publish `LaneTowerSnapshot` and optionalLaneSceneState.towers/hero_status exactlyas spec early;wave_count10|15. Snapshotkeepshero inunits,projectileskindoptional. `createLaneEngine` accepts exactv4/v5 and passes signedv5params only.

- [ ] RED pure tests forspawnsat0/20000/280000,nospawn300000;75IDs;oldv4still50;no tower/v5fieldsinoldraw.
- [ ] REDtowercasesnearestminionthenhero,targetlock/range/cancel180damage/1200interval,friendlyimmunity,projectilepersistsafterrangeexit,stablemultitowerdeath,nonplayerscoring,hero1000HPdeaththen5000respawnfullat(180,420);emptyoldcommands;deathdoesnotpauseworld;oldlifebulletsignored;releasedplayerprojectilecanstilllast-hit;endboundary.
- [ ] Implementoptionalv5ruleswithoutmutatingoldconstants/oldbehavior. NoDOM/Threeinengine. Actorlifeidentityfences andstablehitorder. Unitseparation/heromovement remainscompatible. Donotaddtest-onlyproductionmutators; usesrealinput or testutility setup only.
- [ ] GREENlane engine/pure/combat andoldregressions +TS/lint; full5minmultipleframerate/seedtraces deterministic. Returnfrozeninterfaces+report. Rootreviewwithbaselinepatch.

### Task 3: Host, selection, instructions, reports and browser acceptance

**Ownership:** apps/web/src/esportsAssessments/{AssessmentHost,AssessmentHome,AssessmentSessionPage,AssessmentReportPage,PracticePage,MobaInstructions,reportPresentation}.tsx andtheirtests; hostState.ts anditsregressiontests (explicitcoordinationextension); lane/LaneFeedback.tsx/test; assessmentCSSifneeded; tests/esports-pc.spec.ts,tests/helpers/esports*.ts andtheirunit tests; apps/api/app/cli/{seed_esports_assessments_e2e,summarize_esports_assessments_e2e}.py andtests/cli relevant files. No contracts/engine/rendereredits.

**Interfaces:** consumeTask1isLaneTaskConfig/fixtures andTask2optionalhero_status. Currentv5 laneonly, v4also lane notlegacyfouritem. Freeze existingpractice recoveryterminalreturnselector semantics. v5HUDshowsHP,deaths,ceilremainingrespawnseconds;wholeclockkeepsgoing.

**Coordinated ownership extension:** useAssessmentSession.ts exact familyguard andcreated-modevalidation with existingrecoverytests; v5 actualcreationotherwise rejects newparamskind. This isTask3only.

- [ ] REDrouting/recovery/formal/practice tests forv5andfrozenv4,75denominatorcopy/reportmetrics;deathUIdoesnotphaseinterrupt/resize,respawnafter5swithoutautoinput. Completelegaltestfixtures notcasts toavoidnewfields.
- [ ] Implement familydetection/instructions/label version andHUD; don't enlarge mobileavailability. Keepv4raw/report-specificcopy50, v5raw75+deathstats. Trial20slabelstaysaccurate.
- [ ] Updatecurrentseed/CLI expectations, pinhistoricalfixtures explicitly. ObtainDB55443leaseafterTask1beforeCLIresettingtests.
- [ ] Addopt-in nativev5twoactual300smodes, oneactualtowerdamage/death/respawnnormalinput case andrealblur; noclock/private-state/resultinjection. Preservev4nativehelpers neededhistorical; safeartifactnames/categoriesonly, no credentialsinlogs.
- [ ] FocusedGREENandlint/buildafterdependenciesfreeze; recordrealtestcommands/rootnativeinstructions. Rootownsbrowserexecution/fullweb/deploy. NoGitcommit.

### Task 4: Four original tower models and visual targeting

**Ownership:** lane/{models,renderer}.ts/theirtests; newlane/towerModels.ts/testsifuseful; noStage/Host/simulation/contracts/HUDeditsunlesscoordinateexplicitly.

**Interfaces:** consumeTask2LaneSceneState.towers andhero_status. Fourproceduralfactiontowerswithbody,rotatingweapon/muzzle,windup/releasefeedback; realmissiles recognizabletowerkind. v4scenesrenderexactlyzero towers. Hero model hidden/collapsedwhiledead andrestoredonrespawn, nofakeghostblockingtargets.

- [ ] REDactualThree tests showfourcorrectpositions/factionorientationunderexistingcamera;no towersv4;windup/firingvisible; health/deathlifevisualcorrect; no out-of-canvasgeometry; renderbufferstillresizeonlyonchange.
- [ ] Implementtowersusingexistingresources,noassets/deps. Raycasttowerbodywithunits,nearestvisiblebodyblocks;unattackabletowerreturnsnull;groundfallback/allyocclusion/staleenemyselectiontestsremain. Exclude decorative circles andmuzzleparticle frompick.
- [ ] GREENrealgeometry,frontmostocclusion,resourcecleanup/reuse,andoldrenderer/modeltests. Rootperformsnativevisualafterfreeze. Returnreportandfilelistforreview.

### Task 5: Integrate, verify and release

**Owner:** root coordination/evidencedocs only, workers own product fixes.

- [ ] Createbaselinebeforeedits,focusedbaseline relevanttests; isolatedDBs and ownedservices. Recordpreflightinterfaceownershiptable/rulings, exactworkers.
- [ ] Per-taskreviewpackagesfrombaseline,notHEAD. Freshreviewers; onebroadfinalreviewaftertaskapproval, onecombinedfinalfixwave/scopedreviewifneeded.
- [ ] Allrelatedbackend+CLI,fullfrontend,lint/build/typechecks; nativeactual300s near/ranged, towerdeath/respawn andblur withnormalUI. Preserveinitialfailures. Newqualityfailuresdiagnosewithoutweakeninggate.
- [ ] RunstandardNAS script sourceP port202. Verifyheadunchanged/healthy/backuprestorecomplete/publicversionv5+oldv4+FPSv5 andassetSHA. No productiontestrecords. Cleanonlyownedlocalservices/DB;preserveworktree/evidence. Reportactualcompletionandlimits.
