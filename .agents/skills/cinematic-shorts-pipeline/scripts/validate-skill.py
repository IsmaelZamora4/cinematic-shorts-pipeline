#!/usr/bin/env python3
"""Validate skill files, Markdown links, storyboards, and local pipeline contracts."""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

REQUIRED = [
    "SKILL.md", "LICENSE",
    "references/storytelling.md", "references/art-direction.md",
    "references/cinematography.md", "references/animation-3d.md",
    "references/remotion-integration.md", "references/voice-and-captions.md",
    "references/quality-control.md", "references/cinematic-quality-rubric.md",
    "references/pipeline-security.md",
    "scripts/validate-skill.py", "scripts/validate-storyboard.py",
    "scripts/pipeline.py",
    "scripts/tts-kokoro-worker.py",
    "templates/storyboard.json", "templates/scene-config.json", "templates/render-config.json",
    "templates/pipeline.json",
    "tests/test_validators.py", "docs/sources.md", "docs/skill-candidates.md",
    "docs/lessons-learned.md", "docs/prompt-library.md", "docs/roadmap.md",
    "docs/simulation.md", "docs/benchmark-plan.md", "docs/codex-discovery.md",
    "examples/cave-exploration/plan.md", "examples/cave-exploration/storyboard.json",
    "examples/forest-storm/plan.md", "examples/forest-storm/storyboard.json",
    "examples/abandoned-research-station/plan.md",
    "examples/abandoned-research-station/storyboard.json",
    "examples/night-station/plan.md", "examples/night-station/storyboard.json",
    "examples/cave-exploration/pipeline.json", "tests/test_pipeline.py",
    "tests/test_tts_simulation.py", "tests/test_tts_publication.py",
    "tests/test_tts_kokoro.py",
    "examples/tts-contract/pipeline.json", "examples/tts-contract/storyboard.json",
    "examples/tts-contract/voiceover-script.txt",
    "references/pipeline-orchestration.md",
]
LINK = re.compile(r'!?\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+"[^"]*")?\)')
REFERENCE_DEFINITION = re.compile(
    r"^[ ]{0,3}\[[^\]]+\]:[ \t]*(<[^>]+>|[^ \t]+)"
    r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]+\)))?[ \t]*$",
    re.MULTILINE,
)
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(?<!\\)(`+)(?!`).*?\1(?!`)")
SCHEMA_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")

_storyboard_spec = importlib.util.spec_from_file_location(
    "storyboard_validator", Path(__file__).with_name("validate-storyboard.py")
)
STORYBOARD_VALIDATOR = importlib.util.module_from_spec(_storyboard_spec)
_storyboard_spec.loader.exec_module(STORYBOARD_VALIDATOR)
_pipeline_spec = importlib.util.spec_from_file_location(
    "pipeline_validator", Path(__file__).with_name("pipeline.py")
)
PIPELINE_VALIDATOR = importlib.util.module_from_spec(_pipeline_spec)
_pipeline_spec.loader.exec_module(PIPELINE_VALIDATOR)


def validate_frontmatter(folder_name: str, text: str):
    errors = []
    if not text.startswith("---\n"):
        return ["SKILL.md must start with YAML frontmatter"]
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ["SKILL.md frontmatter is not closed"]
    values = {}
    for line_number, line in enumerate(parts[1].strip().splitlines(), 1):
        if not line.strip():
            continue
        match = FRONTMATTER_LINE.fullmatch(line)
        if not match:
            errors.append(f"invalid frontmatter line {line_number}")
            continue
        key, value = match.groups()
        if key in values:
            errors.append(f"duplicate frontmatter field: {key}")
        else:
            values[key] = value.strip()
    name = values.get("name", "")
    if not name:
        errors.append("frontmatter requires name")
    elif not SCHEMA_NAME.fullmatch(name) or len(name) > 64:
        errors.append("frontmatter name must be a lowercase hyphenated slug of at most 64 characters")
    elif name != folder_name:
        errors.append(f"frontmatter name {name!r} must match folder {folder_name!r}")
    raw_description = values.get("description", "")
    description = raw_description
    if raw_description.startswith('"'):
        try:
            description = json.loads(raw_description)
        except json.JSONDecodeError:
            errors.append("frontmatter description has invalid double-quoted YAML string syntax")
            description = ""
    elif raw_description.startswith("'"):
        if len(raw_description) < 2 or not raw_description.endswith("'"):
            errors.append("frontmatter description has invalid single-quoted YAML string syntax")
            description = ""
        else:
            description = raw_description[1:-1].replace("''", "'")
    elif re.search(r":\s", raw_description):
        errors.append("frontmatter description containing colon-space must be quoted")
    if not isinstance(description, str) or len(description.strip()) < 20:
        errors.append("frontmatter requires a useful description of at least 20 characters")
    return errors


def strip_markdown_code(text: str):
    """Remove fenced, indented, and inline code before scanning links."""
    visible_lines = []
    fence = None
    for line in text.splitlines():
        if fence:
            closing = re.match(r"^ {0,3}([`~]+)\s*$", line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            visible_lines.append("")
            continue
        opening = FENCE_OPEN.match(line)
        if opening:
            fence = opening.group(1)
            visible_lines.append("")
        elif line.startswith("    ") or line.startswith("\t"):
            visible_lines.append("")
        else:
            visible_lines.append(line)
    visible = "\n".join(visible_lines)
    return INLINE_CODE.sub("", visible)


def _validate_local_target(root: Path, markdown: Path, raw_target: str):
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0]
    if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return []
    resolved = (markdown.parent / unquote(target)).resolve()
    relative_markdown = markdown.relative_to(root)
    if not resolved.is_relative_to(root):
        return [f"local link escapes skill directory in {relative_markdown}: {target}"]
    if not resolved.is_file():
        return [f"broken local link in {relative_markdown}: {target}"]
    return []


def validate_markdown_links(root: Path):
    errors = []
    root = root.resolve()
    for markdown in sorted(root.rglob("*.md")):
        try:
            text = strip_markdown_code(markdown.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read Markdown file {markdown.relative_to(root)}: {exc}")
            continue
        for match in LINK.finditer(text):
            errors.extend(_validate_local_target(root, markdown, match.group(1)))
        for match in REFERENCE_DEFINITION.finditer(text):
            errors.extend(_validate_local_target(root, markdown, match.group(1)))
    return errors


def validate_json_files(root: Path):
    errors = []
    for path in sorted(root.rglob("*.json")):
        try:
            STORYBOARD_VALIDATOR.parse_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"invalid JSON in {path.relative_to(root)}: {exc}")
    return errors


def validate_storyboards(root: Path):
    errors = []
    root = root.resolve()
    examples = root / "examples"
    storyboards = sorted(examples.rglob("storyboard.json")) if examples.is_dir() else []
    template = root / "templates" / "storyboard.json"
    if template.is_file():
        storyboards.append(template)
    for path in storyboards:
        relative = path.relative_to(root)
        try:
            data = STORYBOARD_VALIDATOR.parse_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"invalid storyboard JSON in {relative}: {exc}")
            continue
        for error in STORYBOARD_VALIDATOR.validate(data):
            errors.append(f"storyboard validation failed in {relative}: {error}")
    return errors


def validate_pipeline_configs(root: Path):
    errors = []
    root = root.resolve()
    paths = sorted(root.rglob("pipeline.json"))
    for path in paths:
        relative = path.relative_to(root)
        _, pipeline_errors, _, _ = PIPELINE_VALIDATOR.load_and_validate_config(path)
        errors.extend(f"pipeline validation failed in {relative}: {error}" for error in pipeline_errors)
    return errors


def validate(root: Path):
    errors = []
    root = root.resolve()
    for relative in REQUIRED:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")
    entry = root / "SKILL.md"
    if entry.is_file():
        try:
            errors.extend(validate_frontmatter(root.name, entry.read_text(encoding="utf-8")))
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read SKILL.md: {exc}")
    errors.extend(validate_markdown_links(root))
    errors.extend(validate_json_files(root))
    errors.extend(validate_storyboards(root))
    errors.extend(validate_pipeline_configs(root))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=".", help="skill directory")
    args = parser.parse_args()
    errors = validate(Path(args.path))
    if errors:
        print("FAIL: skill validation")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: required files, frontmatter, Markdown links, strict JSON, storyboards, and pipeline configs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
