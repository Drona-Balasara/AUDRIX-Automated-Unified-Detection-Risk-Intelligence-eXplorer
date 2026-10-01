"""Application services invoked by API routes.

Routes stay thin: they call into these functions, which own the database and
domain interactions. This keeps the dependency direction one-way
(API -> services -> database).
"""
