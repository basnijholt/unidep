# pip_indices Implementation Tracking

## Issue: #257 - pip-repositories support

### Goal
Add native support for custom PyPI indices/repositories in unidep to enable:
1. Private package repository support
2. Seamless `unidep install` with custom indices
3. Proper `unidep conda-lock` support with pip_repositories in environment.yaml

## Design Decisions

### Naming Convention
- **Field name**: `pip_indices` (not `pip_repositories` or `indices`)
- **Rationale**: Clear, specific to PyPI, aligns with pip's `--index-url` terminology

### Configuration Structure

#### requirements.yaml
```yaml
name: myproject
channels:
  - conda-forge
pip_indices:
  - https://pypi.org/simple/  # Primary index (--index-url)
  - https://${PIP_USER}:${PIP_PASSWORD}@private.company.com/simple/  # Extra index (--extra-index-url)
dependencies:
  - numpy
  - pip: private-package
```

#### pyproject.toml
```toml
[tool.unidep]
pip_indices = [
    "https://pypi.org/simple/",
    "https://${PIP_USER}:${PIP_PASSWORD}@private.company.com/simple/"
]
dependencies = ["numpy", {pip = "private-package"}]
```

### Key Implementation Details
- First index becomes `--index-url` (primary)
- Additional indices become `--extra-index-url` (supplementary)
- Support environment variable expansion for credentials
- Map to `pip_repositories` in generated environment.yaml for conda-lock compatibility

## Implementation Progress

### Phase 1: Testing Foundation ✅
- [x] Create tracking document
- [x] Unit tests for parsing pip_indices
- [x] Unit tests for environment.yaml generation
- [x] Unit tests for pip command construction
- [x] E2E tests for unidep install with indices
- [x] E2E tests for unidep conda-lock with indices

### Phase 2: Core Implementation ✅
- [x] Extend ParsedRequirements with pip_indices field
- [x] Update parsing logic in _dependencies_parsing.py
- [x] Extend CondaEnvironmentSpec with pip_indices
- [x] Update environment.yaml generation in _conda_env.py

### Phase 3: Command Integration ✅
- [x] Update unidep install to use pip_indices
- [x] Update unidep conda-lock to pass pip_indices
- [x] Update unidep merge to combine pip_indices
- [x] Handle both uv and pip backends

### Phase 4: Documentation & Polish 📝
- [ ] Update README with pip_indices examples
- [ ] Add example project with private indices
- [ ] Document authentication best practices
- [ ] Add migration guide from tool.uv.index

## Test Coverage Checklist

### Unit Tests ✅
- [x] Parse pip_indices from requirements.yaml
- [x] Parse pip_indices from pyproject.toml
- [x] Merge pip_indices from multiple files (deduplication)
- [x] Generate environment.yaml with pip_repositories field
- [x] Construct pip install command with --index-url
- [x] Construct pip install command with --extra-index-url
- [x] Handle empty pip_indices list
- [x] Environment variable expansion in URLs
- [x] URL validation and sanitization

### Integration Tests ✅
- [x] Install package from custom index
- [x] Install with multiple indices (primary + extra)
- [x] Generate conda-lock with pip_repositories
- [x] Merge multiple requirements with different indices
- [x] Install with authentication via env variables
- [x] Fallback behavior when index is unavailable
- [x] Compatibility with existing tool.uv.index config
- [x] Platform-specific dependencies with pip_indices
- [x] Optional dependencies with pip_indices

### Edge Cases ✅
- [x] Duplicate indices across files
- [x] Invalid URL formats
- [x] Missing environment variables in URLs
- [x] Conflicting packages across indices
- [x] Network timeout handling (mock tested)
- [x] Circular dependencies with pip_indices
- [x] Empty strings in indices list

## Files to Modify

### Core Files
1. `unidep/_dependencies_parsing.py`
   - Lines 136-142: Add pip_indices to ParsedRequirements
   - Lines 477-506: Parse pip_indices from configs

2. `unidep/_conda_env.py`
   - Lines 42-49: Add pip_indices to CondaEnvironmentSpec
   - Lines 214-237: Include pip_repositories in environment.yaml

3. `unidep/_cli.py`
   - Lines 1025-1046: Add index flags to pip install
   - Lines 903-933: Add index flags to local installs

4. `unidep/_conda_lock.py`
   - Pass pip_indices through to environment generation

### Test Files
1. `tests/test_dependencies_parsing.py` - NEW tests for pip_indices parsing
2. `tests/test_conda_env.py` - Tests for environment.yaml generation
3. `tests/test_cli.py` - Tests for command construction
4. `tests/test_integration.py` - E2E tests with mock indices

## Notes & Decisions

### Security Considerations
- Never log URLs with embedded credentials
- Support ${VAR} syntax for environment variables
- Document secure credential management

### Backward Compatibility
- pip_indices is optional (empty list by default)
- Existing configs continue working unchanged
- Can coexist with tool.uv.index during migration

### Performance Considerations
- Multiple indices can slow package resolution
- Consider warning if >3 indices configured
- Document index ordering best practices

## Open Questions
- [ ] Should we validate index URLs before use?
- [ ] Should we support named indices for better debugging?
- [ ] How to handle index-specific package pinning?
- [ ] Should we auto-detect and warn about duplicate packages across indices?

## References
- Issue #257: https://github.com/basnijholt/unidep/issues/257
- conda-lock pip_repositories: https://github.com/conda/conda-lock#pip-repositories
- pip index-url docs: https://pip.pypa.io/en/stable/cli/pip_install/#index-url
- uv index config: https://github.com/astral-sh/uv/blob/main/docs/configuration.md
