# Pixi Integration - Implementation Plan

## Core Philosophy
**Let UniDep translate, let Pixi resolve**

UniDep acts as a translator from `requirements.yaml`/`pyproject.toml` to `pixi.toml` format.
Pixi handles all dependency resolution, conflict management, and lock file generation.

## Workflow Overview

```
┌─────────────────────┐     ┌───────────┐     ┌────────────┐     ┌────────────────┐
│ requirements.yaml   │────▶│ pixi.toml │────▶│ pixi.lock  │────▶│ conda-lock.yml │
│ pyproject.toml      │     │           │     │            │     │                │
└─────────────────────┘     └───────────┘     └────────────┘     └────────────────┘
        unidep                unidep             pixi            pixi-to-conda-lock
                            merge --pixi         lock              (optional)
```

## Implementation Phases

### Phase 1: Basic Pixi.toml Generation ✅

- [x] Create `_pixi.py` module for pixi.toml generation
- [x] Add `--pixi` flag to `unidep merge` command
- [x] Support single file → root-level dependencies
- [x] Support multiple files → features/environments
- [x] Handle local editable packages
- [x] Pass through version pins without resolution
- [x] Add comprehensive tests

### Phase 2: Platform Selectors ✅

- [x] Map `# [linux64]` → `[target.linux-64.dependencies]`
- [x] Map `# [osx]` → `[target.osx-64.dependencies]` + `[target.osx-arm64.dependencies]`
- [x] Handle platform-specific pip dependencies
- [x] Add tests for platform-specific generation

### Phase 3: Optional Dependencies → Features

- [ ] Map `optional_dependencies.dev` → `[feature.dev.dependencies]`
- [ ] Create environment combinations (e.g., `dev = ["default", "dev"]`)
- [ ] Support extras syntax in local dependencies

### Phase 4: Lock File Integration (via pixi-to-conda-lock)

- [ ] Add `pixi-to-conda-lock` as optional dependency (`unidep[pixi]`)
- [ ] Add `unidep pixi-lock` command that:
  1. Generates `pixi.toml` (if not exists or `--regenerate`)
  2. Runs `pixi lock` to create `pixi.lock`
  3. Converts to `conda-lock.yml` via `pixi-to-conda-lock`
- [ ] Support `--only-pixi-lock` to skip conda-lock conversion
- [ ] Support monorepo per-package lock files
- [ ] Add `--check-input-hash` equivalent

### Phase 5: Pixi as Install Backend (Optional)

- [ ] Add `--pixi` flag to `unidep install`
- [ ] Use `pixi run` for command execution
- [ ] Leverage pixi's fast resolver for installations

## Key Design Decisions

### 1. No Conflict Resolution
Pixi handles all dependency resolution. UniDep just translates the specification format.

### 2. Platform Mapping

| UniDep Selector | Pixi Target |
|-----------------|-------------|
| `# [linux64]` | `target.linux-64` |
| `# [linux]` | `target.linux-64` + `target.linux-aarch64` |
| `# [osx64]` | `target.osx-64` |
| `# [arm64]` | `target.osx-arm64` |
| `# [osx]` | `target.osx-64` + `target.osx-arm64` |
| `# [win64]` | `target.win-64` |
| `# [unix]` | All linux + osx targets |

### 3. Dependency Type Mapping

| UniDep | Pixi |
|--------|------|
| `- numpy` | `[dependencies] numpy = "*"` |
| `- numpy >=1.20` | `[dependencies] numpy = ">=1.20"` |
| `- conda: scipy` | `[dependencies] scipy = "*"` |
| `- pip: requests` | `[pypi-dependencies] requests = "*"` |
| `local_dependencies` | `[pypi-dependencies] pkg = { path = ".", editable = true }` |

### 4. Optional Dependency Mapping

```yaml
# requirements.yaml
optional_dependencies:
  dev:
    - pytest
    - black
  docs:
    - sphinx
```

```toml
# pixi.toml
[feature.dev.dependencies]
pytest = "*"
black = "*"

[feature.docs.dependencies]
sphinx = "*"

[environments]
default = []
dev = ["dev"]
docs = ["docs"]
all = ["dev", "docs"]
```

## Files Structure

```
unidep/
├── _pixi.py              # Pixi.toml generation (✅ exists)
├── _pixi_lock.py         # Lock file commands (Phase 4)
└── _cli.py               # CLI with --pixi flag (✅ updated)

tests/
└── test_pixi.py          # Pixi tests (✅ exists)
```

## Optional Dependencies Configuration

```toml
# pyproject.toml
[project.optional-dependencies]
pixi = ["pixi-to-conda-lock"]
all = ["...", "pixi-to-conda-lock"]
```

## CLI Commands

### Current (Phase 1)
```bash
# Generate pixi.toml from requirements
unidep merge --pixi
unidep merge --pixi --output my-pixi.toml
unidep merge --pixi --directory ./monorepo --depth 2
```

### Planned (Phase 4)
```bash
# Generate pixi.lock (requires pixi CLI)
unidep pixi-lock

# Generate pixi.lock + conda-lock.yml (requires pixi-to-conda-lock)
unidep pixi-lock --conda-lock

# Full workflow
unidep pixi-lock --directory ./monorepo --depth 2
```

## Testing Strategy

1. **Unit tests**: Validate pixi.toml structure
2. **Integration tests**: Run `pixi lock` on generated files
3. **Monorepo tests**: Test with example/ directory
4. **Round-trip tests**: UniDep → pixi.toml → pixi.lock → conda-lock.yml

## Success Criteria

- [x] Generate valid pixi.toml files
- [x] Pass all unit tests
- [x] Work with single-file projects
- [x] Work with monorepo (multiple requirements files)
- [x] Support platform-specific dependencies
- [ ] Support optional dependencies as features
- [ ] Integrate with pixi-to-conda-lock for lock files
- [ ] Document workflow in README
