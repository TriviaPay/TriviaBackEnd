# Restore Database from Neon Backup

## Quick Steps

### 1. Access Neon Console
- Go to: **https://console.neon.tech**
- Log in with your Neon account

### 2. Navigate to Your Project
- Select your project from the dashboard
- Click on **"Branches"** in the sidebar

### 3. Access Time Travel / Backups
- Click on your branch (usually `main`)
- Go to **"Time Travel"** or **"Backups"** tab
- You'll see a timeline of automatic backups

### 4. Select Backup Point
- Find a backup from **before the tables were deleted**
- Neon typically keeps backups for **7 days**
- Click on the backup point you want to restore

### 5. Create Restore Branch
- Click **"Create Branch"** or **"Restore"** button
- This creates a new branch with data from that backup point
- Give it a name like `restored-backup-2025-11-22`

### 6. Get New Connection String
- Click on the newly created branch
- Go to **"Connection Details"**
- Copy the **Connection String** (it will be different from your current one)

### 7. Update Your Environment
- Open your `.env` file
- Update `DATABASE_URL` with the new connection string from the restored branch
- Save the file

### 8. Verify Data
```bash
# Check if users are restored
python3 -c "from db import engine; from sqlalchemy import text; conn = engine.connect(); result = conn.execute(text('SELECT COUNT(*) FROM users')); print(f'Users: {result.fetchone()[0]}'); conn.close()"
```

### 9. Restart Server
- Restart your FastAPI server
- Test the endpoints

## Alternative: Using Neon API

If you prefer using the API:

```bash
# Set your Neon API key
export NEON_API_KEY="your_api_key"

# List projects
curl -H "Authorization: Bearer $NEON_API_KEY" \
  https://console.neon.tech/api/v2/projects

# List branches for a project
curl -H "Authorization: Bearer $NEON_API_KEY" \
  https://console.neon.tech/api/v2/projects/{project_id}/branches

# Create branch from point in time
curl -X POST \
  -H "Authorization: Bearer $NEON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"branch": {"name": "restored-backup", "parent_id": "branch_id", "point_in_time": "2025-11-22T00:00:00Z"}}' \
  https://console.neon.tech/api/v2/projects/{project_id}/branches
```

## Important Notes

- ⚠️ **Restoring creates a NEW branch** - your current branch data will remain unchanged
- ✅ You can switch between branches by updating `DATABASE_URL`
- 🔄 After restoring, you may want to merge the restored data back to main branch
- 📅 Neon keeps automatic backups for **7 days** by default
- 💾 Consider setting up longer retention if needed

## If You Can't Find Backups

1. Check if you're looking at the correct project/branch
2. Verify your Neon account has access to backups
3. Contact Neon support if backups are missing
4. Check if you have any manual backups (SQL dumps, etc.)

## After Restoration

Once you've restored and updated your `DATABASE_URL`:

1. Verify all tables exist:
   ```bash
   python3 -c "from db import engine; from sqlalchemy import inspect; inspector = inspect(engine); print(f'Tables: {len(inspector.get_table_names())}')"
   ```

2. Check user count:
   ```bash
   python3 -c "from db import engine; from sqlalchemy import text; conn = engine.connect(); result = conn.execute(text('SELECT COUNT(*) FROM users')); print(f'Users: {result.fetchone()[0]}'); conn.close()"
   ```

3. Test endpoints:
   ```bash
   ./test_chat_endpoints_manual.sh YOUR_TOKEN YOUR_TOKEN USER_ID
   ```

