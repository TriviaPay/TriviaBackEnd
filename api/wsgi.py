from api.index import app
import sys

print(f"WSGI Python version: {sys.version}")
print(f"WSGI app type: {type(app)}")

# Export the WSGI application
# Vercel will look for this when using @vercel/python
app = app 