# Pixi Integration - Simple Implementation Plan

## Core Philosophy
**Let UniDep translate, let Pixi resolve**

UniDep should act as a simple translator from `requirements.yaml`/`pyproject.toml` to `pixi.toml` format.
Pixi handles all dependency resolution, conflict management, and lock file generation.

## Current Problem with `pixi` Branch
- **Over-engineering**: Pre-resolves conflicts that Pixi can handle
- **Origin tracking**: Complex system to track where each dependency came from
- **Unnecessary complexity**: ~500+ lines of code for what should be ~100 lines

## New Simple Architecture

### Phase 1: Basic Pixi.toml Generation ✅
- [x] Create minimal `_pixi.py` module (~100 lines)
- [ ] Parse requirements WITHOUT resolution
- [ ] Create features with literal dependencies
- [ ] Generate pixi.toml with proper structure
- [ ] Add `--pixi` flag to merge command

### Phase 2: Pixi Lock Command
- [ ] Add `pixi-lock` subcommand to CLI
- [ ] Simple wrapper around `pixi lock` command
- [ ] Support platform selection
- [ ] Add basic tests

### Phase 3: Monorepo Support (Optional)
- [ ] Generate sub-lock files if needed
- [ ] But let Pixi handle the complexity

## Implementation Details

### 1. Simple Pixi.toml Structure
```python
def generate_pixi_toml(requirements_files, output_file):
    pixi_data = {
        "project": {
            "name": "myenv",
            "channels": channels,
            "platforms": platforms,
        },
        "dependencies": {},
        "pypi-dependencies": {},
    }

    # For monorepo: create features
    if len(requirements_files) > 1:
        pixi_data["feature"] = {}
        pixi_data["environments"] = {}

        for req_file in requirements_files:
            feature_name = req_file.parent.stem
            deps = parse_single_file(req_file)  # NO RESOLUTION!

            pixi_data["feature"][feature_name] = {
                "dependencies": deps.conda,  # Literal copy
                "pypi-dependencies": deps.pip,  # Literal copy
            }

        # Create environments
        all_features = list(pixi_data["feature"].keys())
        pixi_data["environments"]["default"] = all_features
        for feat in all_features:
            pixi_data["environments"][feat.replace("_", "-")] = [feat]
```

### 2. Key Simplifications
- **NO conflict resolution** - Pixi handles this
- **NO origin tracking** - Not needed
- **NO version pinning combination** - Pixi does this
- **NO platform resolution** - Use Pixi's native platform support

### 3. Testing Strategy
- Test pixi.toml generation (structure validation)
- Test CLI integration
- Test with example monorepo
- Let Pixi handle the actual resolution testing

## Files to Create/Modify

### New Files
1. `unidep/_pixi.py` - Simple pixi.toml generation (~100 lines)
2. `tests/test_pixi.py` - Basic tests (~50 lines)

### Modified Files
1. `unidep/_cli.py` - Add `--pixi` flag and `pixi-lock` command
2. `README.md` - Document new Pixi support

## Success Criteria
- [ ] Generate valid pixi.toml files
- [ ] Pass all tests
- [ ] Work with monorepo example
- [ ] Total implementation < 200 lines (vs 500+ in old branch)

## Timeline
- **Hour 1-2**: Basic pixi.toml generation ✅
- **Hour 3-4**: CLI integration and testing
- **Hour 5-6**: Documentation and polish

## Testing Checkpoints
After each major change:
1. Run tests: `pytest tests/test_pixi.py -xvs`
2. Test with monorepo: `unidep merge --pixi tests/simple_monorepo`
3. Validate pixi.toml: `pixi list`

## Commit Strategy
- Commit after each working phase
- Clear commit messages
- Test before each commit
