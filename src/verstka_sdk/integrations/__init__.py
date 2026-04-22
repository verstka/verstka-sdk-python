"""Framework integrations for verstka-sdk (all optional extras).

Each submodule is framework-agnostic to import: it lazy-imports the target
framework and raises a clean ``ImportError`` with install hint if the
corresponding extra is not installed.
"""
