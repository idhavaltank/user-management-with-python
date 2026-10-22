"""HTTP API routers.

Each module in this package exposes a FastAPI ``APIRouter``.  The application
factory in :mod:`app.main` includes those routers and owns cross-cutting
middleware such as CORS, request IDs, and rate limiting.
"""
