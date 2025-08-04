#!/bin/bash
# Test PyPI alternatives feature in different scenarios

set -e  # Exit on error

echo "=== Testing PyPI Alternatives Feature ==="
echo

export UV_NO_CACHE=1

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to extract and show dependencies
show_dependencies() {
    local wheel_file="$1"
    local scenario="$2"

    echo -e "${YELLOW}${scenario}${NC}"
    unzip -p "$wheel_file" '*/METADATA' | grep "Requires-Dist:" || echo "No dependencies found"
    echo
}

# Clean up function
cleanup() {
    rm -rf main_app/dist
    rm -rf test_main_app-0.1.0.dist-info
}

# Start fresh
echo "Cleaning up previous builds..."
cleanup

# Scenario 1: Normal build (local path exists)
echo -e "${GREEN}=== Scenario 1: Normal build (local path exists) ===${NC}"
echo "Expected: Should use file:// URL"
echo
cd main_app
uv build > /dev/null 2>&1
show_dependencies "dist/test_main_app-0.1.0-py2.py3-none-any.whl" "Dependencies in wheel:"
cd ..
cleanup

# Scenario 2: Build with local path missing (simulating CI)
echo -e "${GREEN}=== Scenario 2: Build with local path missing (CI simulation) ===${NC}"
echo "Expected: Should use PyPI alternative (pipefunc)"
echo
mv shared_lib shared_lib.tmp
cd main_app
uv build > /dev/null 2>&1
show_dependencies "dist/test_main_app-0.1.0-py2.py3-none-any.whl" "Dependencies in wheel:"
cd ..
mv shared_lib.tmp shared_lib
cleanup

# Scenario 3: Build with UNIDEP_SKIP_LOCAL_DEPS=1 (local path exists)
echo -e "${GREEN}=== Scenario 3: Build with UNIDEP_SKIP_LOCAL_DEPS=1 (local path exists) ===${NC}"
echo "Expected: Should use PyPI alternative (pipefunc) even though local exists"
echo
cd main_app
UNIDEP_SKIP_LOCAL_DEPS=1 uv build > /dev/null 2>&1
show_dependencies "dist/test_main_app-0.1.0-py2.py3-none-any.whl" "Dependencies in wheel:"
cd ..
cleanup

# Scenario 4: Build with UNIDEP_SKIP_LOCAL_DEPS=1 and local path missing
echo -e "${GREEN}=== Scenario 4: Build with UNIDEP_SKIP_LOCAL_DEPS=1 (local path missing) ===${NC}"
echo "Expected: Should use PyPI alternative (pipefunc)"
echo
mv shared_lib shared_lib.tmp
cd main_app
UNIDEP_SKIP_LOCAL_DEPS=1 uv build > /dev/null 2>&1
show_dependencies "dist/test_main_app-0.1.0-py2.py3-none-any.whl" "Dependencies in wheel:"
cd ..
mv shared_lib.tmp shared_lib
cleanup
