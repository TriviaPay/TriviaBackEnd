# Production Deployment Guide

## Vercel Deployment
1. Deploy FastAPI app to Vercel
2. Set environment variables in Vercel dashboard
3. Configure external cron services

## External Cron Setup

### Using cron-job.org (Free)
1. Create account at cron-job.org
2. Set up three cron jobs:

#### Daily Draw (8:00 PM EST)
- **URL**: `https://your-app.vercel.app/internal/daily-draw`
- **Method**: POST
- **Headers**: `X-Secret: your-internal-secret`
- **Schedule**: `0 20 * * *` (8:00 PM EST)

#### Question Reset (8:01 PM EST)  
- **URL**: `https://your-app.vercel.app/internal/question-reset`
- **Method**: POST
- **Headers**: `X-Secret: your-internal-secret`
- **Schedule**: `1 20 * * *` (8:01 PM EST)

#### Monthly Reset (11:59 PM EST, last day of month)
- **URL**: `https://your-app.vercel.app/internal/monthly-reset`
- **Method**: POST
- **Headers**: `X-Secret: your-internal-secret`
- **Schedule**: `59 23 L * *` (11:59 PM EST, last day of month)

### Using EasyCron (Paid)
1. Create account at easycron.com
2. Set up similar cron jobs with the same URLs and headers
3. Monitor execution logs for reliability

### Using GitHub Actions (Free for public repos)
Create `.github/workflows/cron.yml`:
```yaml
name: Scheduled Tasks
on:
  schedule:
    - cron: '0 20 * * *'  # Daily draw at 8:00 PM EST
    - cron: '1 20 * * *'  # Question reset at 8:01 PM EST
    - cron: '59 23 L * *' # Monthly reset

jobs:
  daily-draw:
    if: github.event.schedule == '0 20 * * *'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Daily Draw
        run: |
          curl -X POST "https://your-app.vercel.app/internal/daily-draw" \
            -H "X-Secret: ${{ secrets.INTERNAL_SECRET }}"

  question-reset:
    if: github.event.schedule == '1 20 * * *'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Question Reset
        run: |
          curl -X POST "https://your-app.vercel.app/internal/question-reset" \
            -H "X-Secret: ${{ secrets.INTERNAL_SECRET }}"

  monthly-reset:
    if: github.event.schedule == '59 23 L * *'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Monthly Reset
        run: |
          curl -X POST "https://your-app.vercel.app/internal/monthly-reset" \
            -H "X-Secret: ${{ secrets.INTERNAL_SECRET }}"
```

## Environment Variables for Vercel

Set these in your Vercel dashboard:

### Required
- `INTERNAL_SECRET`: Secret key for internal API calls
- `ENVIRONMENT`: Set to "production"
- `DATABASE_URL`: Your production database connection string
- `DESCOPE_PROJECT_ID`: Your Descope project ID
- `DESCOPE_MANAGEMENT_KEY`: Your Descope management key
- `STRIPE_PUBLISHABLE_KEY`: Your Stripe publishable key
- `STRIPE_SECRET_KEY`: Your Stripe secret key
- `STRIPE_WEBHOOK_SECRET`: Your Stripe webhook secret

### Optional
- `DRAW_TIME_HOUR`: Default 20 (8 PM)
- `DRAW_TIME_MINUTE`: Default 0
- `DRAW_TIMEZONE`: Default "US/Eastern"

## Testing

### Test Internal Endpoints
```bash
# Test daily draw
curl -X POST "https://your-app.vercel.app/internal/daily-draw" \
  -H "X-Secret: your-internal-secret"

# Test question reset
curl -X POST "https://your-app.vercel.app/internal/question-reset" \
  -H "X-Secret: your-internal-secret"

# Test monthly reset
curl -X POST "https://your-app.vercel.app/internal/monthly-reset" \
  -H "X-Secret: your-internal-secret"

# Test health check
curl "https://your-app.vercel.app/internal/health"
```

### Verify Cron Jobs
1. Check cron service logs for successful executions
2. Monitor Vercel function logs for any errors
3. Verify database changes after scheduled runs

## Monitoring

### Vercel Dashboard
- Monitor function executions
- Check error rates and response times
- View logs for debugging

### Database Monitoring
- Check for daily draw results
- Verify question resets
- Monitor subscription flag resets

### Health Checks
- Use `/internal/health` endpoint for uptime monitoring
- Set up alerts for failed cron executions
- Monitor API response times

## Troubleshooting

### Common Issues

1. **401 Unauthorized**: Check INTERNAL_SECRET matches between cron service and Vercel
2. **500 Internal Server Error**: Check Vercel logs for specific error details
3. **Cron jobs not running**: Verify cron service configuration and timezone settings
4. **Database connection issues**: Verify DATABASE_URL is correct and accessible

### Debug Steps

1. Test endpoints manually with curl
2. Check Vercel function logs
3. Verify environment variables are set correctly
4. Test database connectivity
5. Check cron service configuration

## Security Considerations

1. **Secret Management**: Use strong, unique secrets for INTERNAL_SECRET
2. **HTTPS Only**: Ensure all cron services use HTTPS
3. **Rate Limiting**: Consider implementing rate limiting for internal endpoints
4. **Monitoring**: Set up alerts for unusual activity
5. **Backup**: Regular database backups before scheduled operations
