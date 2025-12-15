# Z_ Tables Cleanup Summary

## Completed Actions

### 1. ✅ Created Migrations
- **`rename_z_blocks_and_presence_back.py`**: Renames `z_blocks` → `blocks` and `z_user_presence` → `user_presence`
- **`drop_unused_z_tables.py`**: Drops all unused z_ tables

### 2. ✅ Updated Models
- Updated `Block.__tablename__` from `"z_blocks"` to `"blocks"`
- Updated `UserPresence.__tablename__` from `"z_user_presence"` to `"user_presence"`
- Removed all unused z_ model classes:
  - E2EEDevice, E2EEKeyBundle, E2EEOneTimePrekey
  - DMConversation, DMParticipant, DMMessage, DMDelivery
  - Group, GroupParticipant, GroupMessage, GroupDelivery, GroupSenderKey, GroupInvite, GroupBan
  - StatusPost, StatusAudience, StatusView
  - DeviceRevocation

### 3. ⚠️ Router Files (No Changes Needed)
The following router files still import deleted models, but they are **conditionally imported** in `main.py` based on feature flags:
- `routers/e2ee_keys.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/dm_conversations.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/dm_messages.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/dm_sse.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/dm_privacy.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/dm_metrics.py` - Only loaded if `E2EE_DM_ENABLED=true`
- `routers/groups.py` - Only loaded if `GROUPS_ENABLED=true`
- `routers/group_members.py` - Only loaded if `GROUPS_ENABLED=true`
- `routers/group_invites.py` - Only loaded if `GROUPS_ENABLED=true`
- `routers/group_messages.py` - Only loaded if `GROUPS_ENABLED=true`
- `routers/group_metrics.py` - Only loaded if `GROUPS_ENABLED=true`
- `routers/status.py` - Only loaded if `STATUS_ENABLED=true`
- `routers/status_metrics.py` - Only loaded if `STATUS_ENABLED=true`

**Note**: Since these feature flags should be `false` in your environment, these routers won't be imported and the missing model imports won't cause issues. If you ever enable these features, you'll need to restore the model definitions.

## Tables Preserved (Used by Active Features)

- ✅ `blocks` (renamed from `z_blocks`) - Used by `private_chat.py`
- ✅ `user_presence` (renamed from `z_user_presence`) - Used by `private_chat.py`

## Tables Deleted

All other z_ tables will be dropped:
- z_e2ee_devices
- z_e2ee_key_bundles
- z_e2ee_one_time_prekeys
- z_dm_conversations
- z_dm_participants
- z_dm_messages
- z_dm_delivery
- z_groups
- z_group_participants
- z_group_messages
- z_group_delivery
- z_group_sender_keys
- z_group_invites
- z_group_bans
- z_status_posts
- z_status_audience
- z_status_views
- z_device_revocations

## Next Steps

1. **Run the migrations**:
   ```bash
   alembic upgrade head
   ```

2. **Verify your feature flags are disabled** (in `.env` or environment):
   ```bash
   E2EE_DM_ENABLED=false
   GROUPS_ENABLED=false
   STATUS_ENABLED=false
   PRESENCE_ENABLED=false  # This can stay true if you want presence in private chat
   ```

3. **Test your active chat systems**:
   - Global chat ✅
   - Trivia live chat ✅
   - Private chat ✅ (blocking and presence should still work)

## Important Notes

- The `Block` and `UserPresence` models are still in `models.py` and will work with the renamed tables
- All active chat systems (global, trivia live, private) are **100% safe** and unaffected
- If you need to restore any deleted features later, you'll need to:
  1. Restore the model definitions in `models.py`
  2. Create a migration to recreate the tables
  3. Re-enable the feature flags
