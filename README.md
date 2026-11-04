# Cinematic Shorts Pipeline

**A planning and validation toolkit for cinematic 3D shorts.** It turns an idea into a reviewable brief, story, storyboard, and local pipeline plan, with a Codex skill discoverable inside the repository.

[![CI](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/actions/workflows/validate.yml)
[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](.github/workflows/validate.yml)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-6f42c1.svg)](.agents/skills/cinematic-shorts-pipeline/SKILL.md)

**Resumen en español:** Skill de Codex y validadores locales para planificar cortos 3D: historia, storyboard, cámara, animación, voz y un DAG de producción. Planifica y valida contratos; no produce medios.

- [Skill](.agents/skills/cinematic-shorts-pipeline/SKILL.md)
- [Examples](.agents/skills/cinematic-shorts-pipeline/examples)
- [Roadmap](.agents/skills/cinematic-shorts-pipeline/docs/roadmap.md)

## Contents

- [Overview](#overview)
- [Features](#features)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Examples](#examples)
- [Validation and testing](#validation-and-testing)
- [Project structure](#project-structure)
- [Architecture and contracts](#architecture-and-contracts)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [Contributing and license](#contributing-and-license)

## Overview

Cinematic Shorts Pipeline is an open-source Codex skill and a small set of local Python validators for planning short-form cinematic 3D projects. It is designed for creators who want a structured production brief, shot-by-shot intent, technical constraints, and review gates before committing to production.

The project separates **planning** from **audiovisual production**. It can validate storyboard and pipeline data and preview a deterministic DAG. It does not execute production stages or create video, images, audio, voices, 3D models, or renders.

## Features

- **Storytelling:** brief, hook, character goal, obstacle, causal beats, reveal, and ending. See [storytelling guidance](.agents/skills/cinematic-shorts-pipeline/references/storytelling.md).
- **Cinematic storyboards:** shot purpose, framing, focus, continuity, light source, and cut intent, with optional per-scene `cinematography` data. See [cinematography](.agents/skills/cinematic-shorts-pipeline/references/cinematography.md).
- **Art direction and animation planning:** visual rules plus optional `animation` beats for rigs, weight, support, root motion, gaze, hands, props, and continuity. These are planning contracts, not rig or motion checks. See [art direction](.agents/skills/cinematic-shorts-pipeline/references/art-direction.md) and [3D animation](.agents/skills/cinematic-shorts-pipeline/references/animation-3d.md).
- **Voice, captions, and timing:** caption intervals and optional `narrative_timing` anchors for beats, pauses, and cuts. The validator checks declared structure and timing; it does not listen to audio or prove synchronization. See [voice and captions](.agents/skills/cinematic-shorts-pipeline/references/voice-and-captions.md).
- **Benchmarking and quality review:** reusable protocols for comparing proposals and reviewing story clarity, stills, and sequences. The protocol is not a performance, retention, or audience-preference measurement. See [benchmark plan](.agents/skills/cinematic-shorts-pipeline/docs/benchmark-plan.md) and [cinematic quality rubric](.agents/skills/cinematic-shorts-pipeline/references/cinematic-quality-rubric.md).
- **Local pipeline planning:** validate a `pipeline_version: 1` DAG, inspect its deterministic order, and validate existing manifests and fingerprints. `plan` and `dry-run` do not execute stages or write files. See [pipeline orchestration](.agents/skills/cinematic-shorts-pipeline/references/pipeline-orchestration.md).
- **Validation and security:** strict JSON and storyboard validation, project-relative path checks, dependency and output checks, and regression tests for read-only CLI behavior. See [pipeline security](.agents/skills/cinematic-shorts-pipeline/references/pipeline-security.md).

## Quick start

### Requirements

- Python 3.12 for the version currently used by GitHub Actions.
- Codex desktop or CLI to invoke the skill. Codex is not needed to run the Python validators.
- No third-party Python packages are required for the included validation commands.

No Blender, TTS engine, Remotion project, FFmpeg, or media assets are needed to validate the planning package.

### Clone and open in Codex

```bash
git clone https://github.com/IsmaelZamora4/cinematic-shorts-pipeline.git
cd cinematic-shorts-pipeline
```

Open the cloned repository root as a Codex project. The skill lives at `.agents/skills/cinematic-shorts-pipeline/SKILL.md` and can be invoked with:

```text
$cinematic-shorts-pipeline
```

Example prompt:

> Use `$cinematic-shorts-pipeline` to plan a 12-second vertical 3D mystery short. Return a brief and a `schema_version: 1` storyboard. Planning only; do not generate media.

### Validate from the repository root

```bash
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-skill.py .agents/skills/cinematic-shorts-pipeline
python -B -m unittest discover -s .agents/skills/cinematic-shorts-pipeline/tests -v
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-storyboard.py .agents/skills/cinematic-shorts-pipeline/examples/night-station/storyboard.json
python -B .agents/skills/cinematic-shorts-pipeline/scripts/pipeline.py validate .agents/skills/cinematic-shorts-pipeline/examples/cave-exploration/pipeline.json
python -B .agents/skills/cinematic-shorts-pipeline/scripts/pipeline.py dry-run .agents/skills/cinematic-shorts-pipeline/examples/cave-exploration/pipeline.json
```

`validate-skill.py` checks the skill package, links, JSON, examples, and storyboard template. The unittest command runs the complete suite.

## How it works

**Idea → brief → storytelling → storyboard → technical plan → validation → reviewable package**

Each stage has an approval gate. The outputs are documents and structured planning data. Passing a validator is not approval to render or produce media; audiovisual work requires separate authorization and review.

## Examples

All examples are fictional planning packages. They contain no generated audiovisual assets.

- [Cave Exploration — The Rope That Ends](.agents/skills/cinematic-shorts-pipeline/examples/cave-exploration/plan.md): a cut rope and rising water turn an apparently empty cave into an active threat; includes a pipeline configuration.
- [Forest Storm — Shelter in the Fallen Tree](.agents/skills/cinematic-shorts-pipeline/examples/forest-storm/plan.md): a storm blocks the route, and a lightning flash reveals nearby shelter.
- [Abandoned Research Station — The Station That Answered](.agents/skills/cinematic-shorts-pipeline/examples/abandoned-research-station/plan.md): a beacon answers from a sealed room in an evacuated polar station.
- [Night Station — La luz tras el sello](.agents/skills/cinematic-shorts-pipeline/examples/night-station/plan.md): a guard follows wet footprints to a sealed door after a lamp goes out and another light answers.

## Validation and testing

The project uses the Python standard library. Run the complete suite and package validator with:

```bash
python -B -m unittest discover -s .agents/skills/cinematic-shorts-pipeline/tests -v
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-skill.py .agents/skills/cinematic-shorts-pipeline
```

GitHub Actions runs the suite and validators on Ubuntu 24.04 and Ubuntu 26.04 with Python 3.12. For v0.7, both jobs passed all **93 tests**, with no skips or failures. On Windows, **91 passed and 2 symlink tests were skipped** because that environment did not allow creating symbolic links. See the [v0.7 workflow run](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/actions/runs/41921199387).

Automated validation checks declared data and contracts. It does not establish cinematic quality. A still requires visual inspection; continuity, timing, performance, and audio synchronization require real media and sequence review.

## Project structure

```text
.
├── .agents/skills/cinematic-shorts-pipeline/
│   ├── docs/          # Roadmap, examples of process, and benchmark protocol
│   ├── examples/      # Fictional plans, storyboards, and pipeline example
│   ├── references/    # Narrative, camera, animation, audio, security, and QA guides
│   ├── scripts/       # Skill, storyboard, and pipeline validators
│   ├── templates/     # Versioned storyboard and pipeline templates
│   └── tests/         # Standard-library regression suite
├── .github/workflows/ # CI validation matrix
├── deliverables/      # Planning packages
├── LICENSE
└── README.md
```

## Architecture and contracts

- **Storyboard:** `schema_version: 1`. `cinematography`, `animation`, and `narrative_timing` are optional additions; earlier storyboards remain valid.
- **Pipeline:** `pipeline_version: 1`, independent of storyboard version. A deterministic DAG describes inputs, outputs, dependencies, resource budgets, tools, and adapter status.
- **Manifest:** `manifest_version: 1` describes a run and records stage states, hashes, fingerprints, and tool versions. The current CLI validates an existing manifest; it does not create or resume runs.
- **Read-only commands:** `validate`, `plan`, `dry-run`, and `validate-manifest` do not launch production tools. `plan` and `dry-run` only display the declared DAG and blockers.

See the [storyboard template](.agents/skills/cinematic-shorts-pipeline/templates/storyboard.json), [pipeline template](.agents/skills/cinematic-shorts-pipeline/templates/pipeline.json), and [technical references](.agents/skills/cinematic-shorts-pipeline/SKILL.md#recursos) for full contracts and guidance.

## Limitations

- Production adapters are not implemented. The DAG is a plan, not an execution engine.
- The project does not automatically generate narration, captions, images, 3D scenes, or videos.
- Validators do not inspect real footage, verify rig compatibility, judge acting, or measure audiovisual timing and quality.
- The benchmarking document defines a repeatable comparison protocol; it contains no new performance or retention measurements.
- Resource availability, versions, compatibility, and licenses must be checked for each project.

## Roadmap

- **v0.7 — Benchmarking & Storytelling:** stable on `main`, merged through [PR #4](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/pull/4), squash commit [`530188c`](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/commit/530188cc37c570939b58e5dfbc64190afb7df0f7).
- **v0.8 — Production Adapters:** planning only. No adapters have been implemented.

See the [full roadmap](.agents/skills/cinematic-shorts-pipeline/docs/roadmap.md).

## Contributing and license

Contributions are welcome. Keep changes focused, add or update tests for behavior changes, and run the validation commands above before opening a pull request. Include the commands run and distinguish automated validation from any visual or audiovisual review.

The project is distributed under the [MIT License](LICENSE). Models, voices, media, and other third-party resources are not bundled; their licenses and attribution requirements must be checked separately before use.
