# Store Router Cleanup Summary

## ✅ Completed Actions

### Endpoints Deleted
1. **POST /store/gameplay-boosts** - Purchase and use gameplay boosts
2. **GET /store/boost-configs** - Get all boost configurations (non-admin)
3. **GET /store/items** - Get all store items from config

### Endpoints Kept
1. **POST /store/buy-gems** - Buy gems with wallet balance
2. **GET /store/gem-packages** - Get all gem packages
3. **POST /store/admin/gem-packages** - Create gem package (admin)
4. **PUT /store/admin/gem-packages/{package_id}** - Update gem package (admin)
5. **DELETE /store/admin/gem-packages/{package_id}** - Delete gem package (admin)
6. **POST /store/admin/boost-configs** - Create boost config (admin)
7. **PUT /store/admin/boost-configs/{boost_type}** - Update boost config (admin)
8. **DELETE /store/admin/boost-configs/{boost_type}** - Delete boost config (admin)

### Code Cleanup
- Removed unused imports: `Trivia`, `TriviaQuestionsDaily`, `TriviaQuestionsEntries`, `TriviaUserDaily`
- Removed unused imports: `get_active_draw_date`, `random`, `func`, `pytz`, `os`, `json`, `FilePath`
- Removed unused imports: `Query`, `Literal`, logging helpers (`log_info`, `log_warning`, etc.)
- Removed unused models: `PurchaseRequest`, `UseBoostRequest`
- Removed unused helper function: `get_today_in_app_timezone()`
- Removed unused config loading: `store_config` from `config/store_items.json`
- Fixed `create_gem_package` to use `price_minor` instead of `price_usd` (since `price_usd` is a computed property)

---

## 📊 Table Analysis Results

### ✅ Tables to KEEP (All Required)

1. **GemPackageConfig** - ✅ REQUIRED
   - Used by: `buy-gems`, `get-gem-packages`, all admin gem-packages endpoints

2. **UserGemPurchase** - ✅ REQUIRED
   - Used by: `buy-gems` (records purchases)

3. **BoostConfig** - ✅ REQUIRED
   - Used by: Admin boost-configs endpoints (create/update/delete)
   - **Note**: Even though gameplay-boosts endpoint was deleted, admin endpoints still need this table to manage boost configurations

### ❌ Tables Used by Deleted Endpoints (But Cannot Delete)

4. **Trivia** - ❌ CANNOT DELETE
   - Used by deleted endpoint: `gameplay-boosts`
   - **BUT ALSO USED BY**: 
     - `routers/rewards.py` - For checking correct answers
     - `routers/internal.py` - For draws
     - `routers/admin.py` - Admin operations
     - `routers/trivia_free_mode.py` - Free mode trivia
     - `routers/trivia_five_dollar_mode.py` - Bronze mode trivia
     - `routers/trivia_silver_mode.py` - Silver mode trivia

5. **TriviaUserDaily** - ❌ CANNOT DELETE
   - Used by deleted endpoint: `gameplay-boosts`
   - **BUT ALSO USED BY**:
     - `routers/rewards.py` - For checking if user answered correctly
     - `routers/internal.py` - For draws and eligibility checks

6. **TriviaQuestionsDaily** - ❌ CANNOT DELETE
   - Used by deleted endpoint: `gameplay-boosts`
   - **BUT ALSO USED BY**:
     - `routers/internal.py` - For draws and question allocation

7. **TriviaQuestionsEntries** - ❌ CANNOT DELETE
   - Used by deleted endpoint: `gameplay-boosts`
   - **BUT ALSO USED BY**:
     - `routers/entries.py` - GET /entries endpoint
     - `routers/internal.py` - For draws and eligibility

---

## 🎯 Final Summary

### Tables Status
**NO TABLES CAN BE DELETED** because:
1. All tables used by kept endpoints are required (GemPackageConfig, UserGemPurchase, BoostConfig)
2. All trivia tables are actively used in other routers (rewards, internal, entries, admin, trivia mode routers)

### Endpoints Status
- ✅ **3 endpoints deleted** (gameplay-boosts, get-boost-configs, get-items)
- ✅ **8 endpoints kept** (buy-gems, get-gem-packages, 6 admin endpoints)
- ✅ **All unused code cleaned up**

The cleanup is complete. The store router now only contains gem package management and admin endpoints for boost configurations.
