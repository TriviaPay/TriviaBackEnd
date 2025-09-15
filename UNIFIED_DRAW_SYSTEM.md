# Unified Draw System Implementation

## Overview
The draw system has been unified to ensure both scheduled and admin-triggered draws use the same logic, eligibility criteria, prize pool calculation, and winner selection methods.

## Key Changes

### 1. Database Schema Updates
- **Added `daily_eligibility_flag`** to `users` table to track if user answered all 3 questions correctly for the day
- **Created `user_question_answers`** table to track individual question responses
- **Created `company_revenue`** table (monthly) to track revenue from subscriber fees

### 2. Unified Eligibility Criteria
Users are eligible for daily draws if:
- `User.subscription_flag == True` (subscribed for that month)
- `User.daily_eligibility_flag == True` (answered all 3 trivia questions correctly that day)

### 3. Prize Pool Calculation
- **If subscriber_count < 200**: `monthly_prize_pool = subscriber_count * 4.3`
- **If subscriber_count >= 200**: 
  - `monthly_prize_pool = subscriber_count * 3.526`
  - `company_revenue = subscriber_count * 0.774` (added to company_revenue table)
- **Daily prize pool**: `monthly_prize_pool / days_in_month`

### 4. Winner Count Determination
Uses tiered system based on participant count:
- <50 participants: 1 winner
- <100: 3 winners  
- <200: 5 winners
- <300: 7 winners
- <400: 11 winners
- <500: 13 winners
- <600: 17 winners
- <700: 19 winners
- <800: 23 winners
- <900: 29 winners
- <1000: 31 winners
- <1100: 37 winners
- <1200: 41 winners
- <1300: 43 winners
- <2000: 47 winners
- 2000+: 53 winners (cap)

### 5. Prize Distribution
Uses harmonic distribution:
- 1st place gets largest share: `(1/1) / harmonic_sum * total_prize`
- 2nd place: `(1/2) / harmonic_sum * total_prize`
- 3rd place: `(1/3) / harmonic_sum * total_prize`
- And so on...

### 6. Winner Selection
- If participants ≤ winners: everyone wins
- Otherwise: random selection using `random.sample()`

### 7. Configuration
Both scheduled and admin draws now use `TriviaDrawConfig` table:
- `is_custom`: boolean to enable custom winner count
- `custom_winner_count`: override the calculated winner count

### 8. Automated Reset Jobs
- **Daily Reset**: After each draw, `daily_eligibility_flag` is reset to `False` for all users
- **Monthly Reset**: At 12:01 AM EST on the last day of each month, `subscription_flag` is reset to `False` for all users

### 9. Scheduler Updates
- **Daily draw**: Runs at configured time (default 8 PM EST)
- **Monthly subscription reset**: Runs at 12:01 AM EST on last day of month
- **Dynamic rescheduling**: When draw time is updated, scheduler automatically reschedules

## Files Modified

### Core Logic
- `rewards_logic.py`: Unified draw logic with all helper functions
- `models.py`: Added new models and daily_eligibility_flag column

### API Endpoints
- `routers/rewards.py`: Admin endpoints now use unified logic
- `routers/admin.py`: Uses same draw configuration

### Scheduler
- `scheduler.py`: Added monthly reset job and dynamic rescheduling

### Migration
- `migrations/add_unified_draw_system.py`: Database schema updates

## API Usage

### Admin Endpoints
- `POST /admin/trigger-draw`: Manually trigger draw (uses unified logic)
- `PUT /admin/custom-winner-count`: Set custom winner count
- `PUT /admin/reset-winner-logic`: Reset to calculated winner count

### User Eligibility Tracking
To mark a user as eligible after they answer questions correctly:
```python
from rewards_logic import update_user_eligibility
update_user_eligibility(db, user_account_id, draw_date)
```

### Manual Resets (Admin Use)
```python
from rewards_logic import reset_daily_eligibility_flags, reset_monthly_subscriptions

# Reset daily eligibility (normally done after each draw)
reset_daily_eligibility_flags(db)

# Reset monthly subscriptions (normally done automatically)  
reset_monthly_subscriptions(db)
```

## Environment Variables
- `DRAW_TIME_HOUR`: Hour for daily draw (default: 20)
- `DRAW_TIME_MINUTE`: Minute for daily draw (default: 0)
- `DRAW_TIMEZONE`: Timezone for draws (default: US/Eastern)

## Testing Notes
- Removed arbitrary test user selection
- All eligibility is now based on actual subscription and question-answering status
- Company revenue tracking provides transparency into fee allocation

## Next Steps
1. Update trivia question answering logic to call `update_user_eligibility()` when users complete all 3 questions correctly
2. Update subscription management to set `subscription_flag = True` when users subscribe
3. Monitor company revenue table for financial reporting
4. Consider adding admin endpoints to view revenue reports 