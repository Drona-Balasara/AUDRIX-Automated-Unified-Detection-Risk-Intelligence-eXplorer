"""SAT-SA backend application package.

SAT-SA (Security Assessment & Supervisory Analytics) is an evidence-driven
security operations supervisory analytics platform. This package contains the
FastAPI application and its supporting modules.

The package is organized by responsibility:

- ``core``      application configuration and logging setup
- ``api``       versioned HTTP routing
- ``db``        SQLAlchemy engine, session factory, and declarative base
- ``models``    ORM models (minimal in Phase 1)
- ``schemas``   Pydantic request/response models
- ``services``  application logic invoked by API routes
- ``analytics`` reserved for the later supervisory-analytics pipeline
- ``utils``     small, dependency-free helpers
"""
