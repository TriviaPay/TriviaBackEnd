# Legacy Tables Analysis

## Summary of All Tables and Their Status

### ✅ **CONFIRMED LEGACY TABLES (Can be deleted)**

1. **`updates`** (UpdatePost)
   - **Status**: ❌ LEGACY - Not used anywhere
   - **Usage**: None found
   - **Action**: DELETE

2. **`letters`** (Letter)
   - **Status**: ❌ LEGACY - Not used anymore
   - **Usage**: Only in test files (conftest.py) - already removed from models
   - **Action**: DELETE (migration already created)

3. **`badges`** (Badge)
   - **Status**: ❌ LEGACY - Merged into trivia_mode_config
   - **Usage**: All references updated to use TriviaModeConfig
   - **Action**: DELETE (migration in progress)

4. **`winners_draw_config`** (TriviaDrawConfig)
   - **Status**: ❌ LEGACY - Replaced by mode-specific draws
   - **Usage**: Only in `rewards_logic.py` (legacy draw system)
   - **Action**: DELETE (already removed from models)

5. **`winners_draw_results`** (TriviaQuestionsWinners)
   - **Status**: ❌ LEGACY - Replaced by mode-specific winner tables
   - **Usage**: Only in `rewards_logic.py` (legacy draw system)
   - **Action**: DELETE (already removed from models)

6. **`withdrawals`** (Withdrawal)
   - **Status**: ❌ LEGACY - Replaced by `withdrawal_requests` (WithdrawalRequest)
   - **Usage**: None found in routers
   - **Action**: DELETE

---

### ⚠️ **POTENTIALLY LEGACY TABLES (Need verification)**

7. **`trivia`** (Trivia)
   - **Status**: ⚠️ POTENTIALLY LEGACY
   - **Current Usage**:
     - `rewards_logic.py` - Legacy draw system (get_eligible_participants, perform_draw)
     - `cleanup_unused_questions.py` - Question cleanup script
     - `routers/internal.py` - Question reset endpoint (uses TriviaQuestionsDaily which references Trivia)
   - **New System**: Uses mode-specific question tables:
     - `trivia_questions_free_mode`
     - `trivia_questions_bronze_mode`
     - `trivia_questions_silver_mode`
   - **Note**: If legacy draw system is fully removed, this table becomes unused
   - **Action**: VERIFY if legacy draw system is still needed

8. **`trivia_questions_daily`** (TriviaQuestionsDaily)
   - **Status**: ⚠️ POTENTIALLY LEGACY
   - **Current Usage**:
     - `routers/internal.py` - Question reset endpoint (`/internal/question-reset`)
     - References `trivia.question_number` (foreign key)
   - **New System**: Uses mode-specific daily tables:
     - `trivia_questions_free_mode_daily`
     - `trivia_questions_bronze_mode_daily`
     - `trivia_questions_silver_mode_daily`
   - **Note**: Only used for legacy question pool management
   - **Action**: VERIFY if question reset endpoint is still needed for legacy system

9. **`trivia_user_daily`** (TriviaUserDaily)
   - **Status**: ⚠️ POTENTIALLY LEGACY
   - **Current Usage**:
     - `rewards_logic.py` - Legacy draw system (get_eligible_participants)
     - `routers/internal.py` - Trivia reminder endpoint (checks if user answered correctly)
   - **New System**: Uses mode-specific user daily tables:
     - `trivia_user_free_mode_daily`
     - `trivia_user_bronze_mode_daily`
     - `trivia_user_silver_mode_daily`
   - **Note**: Used by legacy draw eligibility checks
   - **Action**: VERIFY if legacy draw system is still needed

10. **`trivia_questions_entries`** (TriviaQuestionsEntries)
    - **Status**: ⚠️ POTENTIALLY LEGACY
    - **Current Usage**:
      - `routers/entries.py` - GET `/entries` endpoint (returns all entries)
      - `rewards_logic.py` - Legacy draw system (cross-check for eligibility)
    - **New System**: Mode-specific systems don't use this
    - **Note**: Only used by legacy draw system and a simple entries endpoint
    - **Action**: VERIFY if `/entries` endpoint is still needed

---

### ✅ **ACTIVE TABLES (NOT LEGACY - Keep)**

11. **`user_daily_rewards`** (UserDailyRewards)
    - **Status**: ✅ ACTIVE
    - **Usage**: `routers/rewards.py` - Daily login rewards (GET/POST `/rewards/daily-login`)
    - **Action**: KEEP

12. **`company_revenue`** (CompanyRevenue)
    - **Status**: ✅ ACTIVE
    - **Usage**: `rewards_logic.py` - Prize pool calculation
    - **Action**: KEEP

13. **`withdrawal_requests`** (WithdrawalRequest)
    - **Status**: ✅ ACTIVE (replaces `withdrawals`)
    - **Usage**: Wallet/stripe withdrawal system
    - **Action**: KEEP

14. **All Mode-Specific Tables** (Active)
    - `trivia_mode_config` ✅
    - `trivia_questions_free_mode` ✅
    - `trivia_questions_free_mode_daily` ✅
    - `trivia_user_free_mode_daily` ✅
    - `trivia_free_mode_winners` ✅
    - `trivia_free_mode_leaderboard` ✅
    - `trivia_questions_bronze_mode` ✅
    - `trivia_questions_bronze_mode_daily` ✅
    - `trivia_user_bronze_mode_daily` ✅
    - `trivia_bronze_mode_winners` ✅
    - `trivia_bronze_mode_leaderboard` ✅
    - `trivia_questions_silver_mode` ✅
    - `trivia_questions_silver_mode_daily` ✅
    - `trivia_user_silver_mode_daily` ✅
    - `trivia_silver_mode_winners` ✅
    - `trivia_silver_mode_leaderboard` ✅

15. **All Other Active Tables**
    - `users` ✅
    - `avatars` ✅
    - `frames` ✅
    - `user_avatars` ✅
    - `user_frames` ✅
    - `gem_package_config` ✅
    - `user_gem_purchases` ✅
    - `subscription_plans` ✅
    - `user_subscriptions` ✅
    - `blocks` ✅
    - `user_presence` ✅
    - `stripe_webhook_events` ✅
    - `stripe_reconciliation_snapshots` ✅
    - `global_chat_messages` ✅
    - `private_chat_conversations` ✅
    - `private_chat_messages` ✅
    - `trivia_live_chat_messages` ✅
    - `global_chat_viewers` ✅
    - `trivia_live_chat_viewers` ✅
    - `trivia_live_chat_likes` ✅
    - `onesignal_players` ✅
    - `notifications` ✅
    - `wallet_transactions` ✅
    - `iap_receipts` ✅
    - `chat_mute_preferences` ✅

---

## Recommendations

### Immediate Deletions (Confirmed Legacy):
1. ✅ `updates` - DELETE
2. ✅ `letters` - DELETE (migration in progress)
3. ✅ `badges` - DELETE (migration in progress)
4. ✅ `winners_draw_config` - DELETE (already removed from models)
5. ✅ `winners_draw_results` - DELETE (already removed from models)
6. ✅ `withdrawals` - DELETE (replaced by withdrawal_requests)

### Needs Verification (Potentially Legacy):
7. ⚠️ `trivia` - Check if legacy draw system is still needed
8. ⚠️ `trivia_questions_daily` - Check if question reset endpoint is still needed
9. ⚠️ `trivia_user_daily` - Check if legacy draw system is still needed
10. ⚠️ `trivia_questions_entries` - Check if `/entries` endpoint is still needed

### Keep (Active):
- All mode-specific tables
- `user_daily_rewards` (daily login)
- `company_revenue` (prize pool)
- `withdrawal_requests` (withdrawals)
- All other active tables

---

## Questions to Answer:

1. **Is the legacy draw system (`rewards_logic.py`) still being used?**
   - ✅ YES - Still imported in:
     - `routers/draw.py` - Uses `calculate_prize_pool` and `get_eligible_participants`
     - `routers/rewards.py` - Uses `calculate_prize_pool` and `get_eligible_participants`
   - ⚠️ BUT - The `perform_draw` function (which uses TriviaDrawConfig/TriviaQuestionsWinners) is NOT used anymore
   - **Status**: Partially legacy - `perform_draw` is unused, but helper functions are still used
   - **Action**: Can remove `perform_draw`, `get_draw_config` functions, but keep helper functions

2. **Is the `/entries` endpoint still needed?**
   - Simple GET endpoint that returns all TriviaQuestionsEntries
   - **Decision needed**: Is this endpoint still used by frontend?

3. **Is the `/internal/question-reset` endpoint still needed?**
   - Uses `TriviaQuestionsDaily` and `Trivia` tables
   - **Decision needed**: Is this for legacy system or still active?

4. **Is the trivia reminder system still using `TriviaUserDaily`?**
   - Used in `/internal/trivia-reminder`
   - **Decision needed**: Should this use mode-specific tables instead?

---

## Final Summary

### ✅ **ALL LEGACY TABLES DELETED (10 tables total)**:

**Confirmed Legacy (6 tables):**
1. ✅ `updates` - DELETED
2. ✅ `letters` - DELETED  
3. ✅ `badges` - DELETED (merged into trivia_mode_config)
4. ✅ `winners_draw_config` - DELETED (legacy draw config)
5. ✅ `winners_draw_results` - DELETED (legacy draw winners)
6. ✅ `withdrawals` - DELETED (replaced by withdrawal_requests)

**Potentially Legacy (4 tables - VERIFIED AND DELETED):**
7. ✅ `trivia` - DELETED (legacy question system)
8. ✅ `trivia_questions_daily` - DELETED (legacy question pool)
9. ✅ `trivia_user_daily` - DELETED (legacy draw eligibility)
10. ✅ `trivia_questions_entries` - DELETED (legacy entries endpoint)

### ✅ Active Tables (Keep):
- All mode-specific tables
- `user_daily_rewards` (daily login)
- `company_revenue` (prize pool)
- `withdrawal_requests` (withdrawals)
- All other tables listed above

---

## ✅ **DELETION COMPLETE**

All 10 legacy tables have been:
- ✅ Removed from `models.py`
- ✅ All code references updated
- ✅ Migration created to drop tables
- ✅ Endpoints using legacy tables removed/updated

**Migration file**: `migrations/versions/c5445efdd8f0_merge_badges_into_trivia_mode_config_and_drop_legacy_tables.py`

**Next step**: Run `alembic upgrade head` to apply the migration.
