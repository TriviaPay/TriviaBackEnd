#!/bin/bash
# Run all payment endpoint tests

echo "🧪 Running Payment Endpoint Tests"
echo "=================================="
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest is not installed. Install it with: pip install pytest pytest-asyncio"
    exit 1
fi

# Run tests with verbose output
echo "📋 Test Files:"
echo "  - test_iap_endpoints.py"
echo ""

pytest tests/test_iap_endpoints.py \
       -v \
       --tb=short \
       --color=yes

echo ""
echo "✅ Tests completed!"
