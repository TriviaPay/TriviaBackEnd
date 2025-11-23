# Test Issues and Fixes

## Issues Found

### 1. ✅ Fixed: Missing `greenlet` dependency
**Error:** `ValueError: the greenlet library is required to use this function. No module named 'greenlet'`

**Fix:** Added `greenlet>=3.0.0` to `requirements.txt`

### 2. ✅ Fixed: Async test configuration
**Error:** Tests skipped with `PytestUnhandledCoroutineWarning`

**Fix:** 
- Added `asyncio_mode = auto` to `pytest.ini`
- Added `asyncio_default_fixture_loop_scope = function` to `pytest.ini`
- Added `@pytest.mark.asyncio` decorators to all async test functions

### 3. ✅ Fixed: Using synchronous TestClient for async endpoints
**Error:** Tests using `TestClient` which doesn't support async

**Fix:** 
- Replaced `TestClient` with `httpx.AsyncClient`
- Updated all `client.get/post` calls to `await ac.get/post` within `async with AsyncClient(...) as ac:` blocks

### 4. ⚠️ Needs Fix: Dependency mocking approach
**Issue:** Using `@patch` doesn't work for FastAPI dependencies

**Solution:** Use `app.dependency_overrides` instead:

```python
from app.dependencies import get_current_user
from app.db import get_async_db

# Override dependencies
app.dependency_overrides[get_current_user] = lambda: mock_user
app.dependency_overrides[get_async_db] = lambda: mock_db_session

# Run test
async with AsyncClient(app=app, base_url="http://test") as ac:
    response = await ac.get("/api/v1/wallet/me")

# Clean up
app.dependency_overrides.clear()
```

### 5. ⚠️ Needs Fix: Mock database sessions
**Issue:** Need to properly mock async database operations

**Solution:** Create proper async mocks:

```python
mock_db_session = AsyncMock()
# Mock execute results
mock_result = MagicMock()
mock_result.scalar_one_or_none.return_value = None
mock_result.scalars.return_value.all.return_value = []
mock_db_session.execute = AsyncMock(return_value=mock_result)
```

## Quick Fix Summary

1. ✅ Install greenlet: `pip install greenlet>=3.0.0`
2. ✅ Updated pytest.ini with asyncio config
3. ✅ Updated all test files to use AsyncClient
4. ⚠️ Need to update dependency mocking to use `app.dependency_overrides`
5. ⚠️ Need to properly mock async database operations

## Running Tests After Fixes

```bash
# Install missing dependency
pip install greenlet>=3.0.0

# Run tests
pytest tests/test_wallet_endpoints.py -v
```

## Next Steps

1. Update all test files to use `app.dependency_overrides` instead of `@patch` for FastAPI dependencies
2. Ensure all async database operations are properly mocked
3. Test that all 32 tests run successfully

