"""Business-logic services for the Guardian backend.

Modules are imported lazily by the routes (e.g. the classifier loads torch and
the model weights only on first use), so this package does not re-export them.
"""
