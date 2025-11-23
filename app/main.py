"""
TriviaPay Wallet API - Async FastAPI Application
"""
from fastapi import FastAPI
from app.routers import wallet, stripe_connect, admin_withdrawals, iap, admin_iap_products, stripe_webhook

app = FastAPI(
    title="TriviaPay Wallet API",
    description="Async wallet system with Stripe Connect and IAP support",
    version="1.0.0"
)

# Include routers
app.include_router(wallet.router)
app.include_router(stripe_connect.router)
app.include_router(admin_withdrawals.router)
app.include_router(iap.router)
app.include_router(admin_iap_products.router)
app.include_router(stripe_webhook.router)

