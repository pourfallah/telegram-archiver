"""recovery_v2 — thin CLI entry points for the phased sampling pipeline.

These only parse CLI args and delegate to ``recovery.pipeline``; all real logic
lives in the shared pipeline (single implementation for CLI == tests == app).
"""