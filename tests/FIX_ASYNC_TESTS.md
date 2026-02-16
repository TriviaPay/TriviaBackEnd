# Fixing Async Test Issues

## Issues Found

1. **Tests are using `async def` but `TestClient` is synchronous**
   - Solution: Use `httpx.AsyncClient` instead

2. **Missing `@pytest.mark.asyncio` decorators**
   - Solution: Add `@pytest.mark.asyncio` to all async test functions

3. **pytest-asyncio configuration missing**
   - Solution: Add `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = function` to `pytest.ini`

## Files to Fix

1. ✅ `pytest.ini` - Fixed
2. ⚠️ `tests/test_iap_endpoints.py` - Needs fixing

## Pattern to Apply

Replace:
```python
async def test_something(...):
    ...
    response = client.get(...)
    # or
    response = client.post(...)
```

With:
```python
@pytest.mark.asyncio
async def test_something(...):
    ...
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get(...)
        # or
        response = await ac.post(...)
```

## Quick Fix Script

Run this to fix all test files:

```python
import re
import os

test_files = [
    'tests/test_iap_endpoints.py'
]

for file_path in test_files:
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Add pytest.mark.asyncio before async def test_ functions
    content = re.sub(
        r'(\s+)(async def test_\w+)',
        r'\1@pytest.mark.asyncio\n\1\2',
        content
    )
    
    # Replace client.get/post with async versions
    content = re.sub(
        r'(\s+)(response = client\.(get|post)\([^)]+\))',
        r'\1async with AsyncClient(app=app, base_url="http://test") as ac:\n\1    response = await ac.\3(\2.replace("client.", "").replace("client.get", "ac.get").replace("client.post", "ac.post"))',
        content
    )
    
    # Better: replace properly
    content = re.sub(
        r'(\s+)response = client\.(get|post)\(([^)]+)\)',
        lambda m: f'{m.group(1)}async with AsyncClient(app=app, base_url="http://test") as ac:\n{m.group(1)}    response = await ac.{m.group(2)}({m.group(3)})',
        content
    )
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f'Fixed {file_path}')
```
