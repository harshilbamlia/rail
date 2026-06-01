import sys, os
# Add project root to path so app.py can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # re-export FastAPI app for Vercel ASGI handler
