# Store Router Cleanup Analysis

## Endpoints to KEEP

### Admin Endpoints (All)
- `POST /store/admin/gem-packages` - Create gem package
- `PUT /store/admin/gem-packages/{package_id}` - Update gem package
- `DELETE /store/admin/gem-packages/{package_id}` - Delete gem package
- `POST /store/admin/boost-configs` - Create boost config
- `PUT /store/admin/boost-configs/{boost_type}` - Update boost config
- `DELETE /store/admin/boost-configs/{boost_type}` - Delete boost config

### User Endpoints
- `POST /store/buy-gems` - Buy gems with wallet balance
- `GET /store/gem-packages` - Get all gem packages

## Endpoints to DELETE

1. `POST /store/gameplay-boosts` - Purchase and use gameplay boosts
2. `GET /store/boost-configs` - Get all boost configurations (non-admin)
3. `GET /store/items` - Get all store items from config

---

## Table Usage Analysis

### Tables Used by KEPT Endpoints

#### ✅ **GemPackageConfig** - KEEP
- Used by: `buy-gems`, `get-gem-packages`, all admin gem-packages endpoints
- **Status**: REQUIRED - Cannot delete

#### ✅ **UserGemPurchase** - KEEP
- Used by: `buy-gems` (records purchases)
- **Status**: REQUIRED - Cannot delete

#### ✅ **BoostConfig** - KEEP
- Used by: Admin boost-configs endpoints (create/update/delete)
- Also used by: `gameplay-boosts` (DELETE), `get-boost-configs` (DELETE)
- **Status**: REQUIRED - Admin endpoints need this table to manage boost configurations

### Tables Used by DELETED Endpoints Only

#### ❌ **Trivia** - KEEP (Used elsewhere)
- Used by: `gameplay-boosts` (DELETE endpoint)
- **BUT ALSO USED BY**: 
  - `routers/rewards.py` - For checking correct answers
  - `routers/internal.py` - For draws
  - `routers/admin.py` - Admin operations
  - `routers/trivia_free_mode.py` - Free mode trivia
  - `routers/trivia_five_dollar_mode.py` - Bronze mode trivia
  - `routers/trivia_silver_mode.py` - Silver mode trivia
- **Status**: CANNOT DELETE - Used in multiple other routers

#### ❌ **TriviaUserDaily** - KEEP (Used elsewhere)
- Used by: `gameplay-boosts` (DELETE endpoint)
- **BUT ALSO USED BY**:
  - `routers/rewards.py` - For checking if user answered correctly
  - `routers/internal.py` - For draws and eligibility checks
- **Status**: CANNOT DELETE - Used in rewards and internal routers

#### ❌ **TriviaQuestionsDaily** - KEEP (Used elsewhere)
- Used by: `gameplay-boosts` (DELETE endpoint)
- **BUT ALSO USED BY**:
  - `routers/internal.py` - For draws and question allocation
- **Status**: CANNOT DELETE - Used in internal router

#### ❌ **TriviaQuestionsEntries** - KEEP (Used elsewhere)
- Used by: `gameplay-boosts` (DELETE endpoint)
- **BUT ALSO USED BY**:
  - `routers/entries.py` - GET /entries endpoint
  - `routers/internal.py` - For draws and eligibility
- **Status**: CANNOT DELETE - Used in entries and internal routers

---

## Summary

### ✅ Tables to KEEP (All Required)
1. **GemPackageConfig** - Required for gem packages
2. **UserGemPurchase** - Required for purchase tracking
3. **BoostConfig** - Required for admin boost config management
4. **Trivia** - Used in rewards, internal, admin, trivia mode routers
5. **TriviaUserDaily** - Used in rewards, internal routers
6. **TriviaQuestionsDaily** - Used in internal router
7. **TriviaQuestionsEntries** - Used in entries, internal routers

### ❌ Tables to DELETE
**NONE** - All tables are either:
- Required by kept endpoints (GemPackageConfig, UserGemPurchase, BoostConfig)
- Used in other routers (all Trivia tables)

---

## Conclusion

**No tables can be deleted** because:
1. All tables used by kept endpoints are required
2. All trivia tables are actively used in other routers (rewards, internal, entries, admin, trivia mode routers)

The cleanup will only remove endpoints, not tables.
