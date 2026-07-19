"""External-service integrations.

Each integration lives in its own subpackage and follows the project's
config-flag swap convention: the concrete provider is chosen at runtime from a
typed Settings flag, so a new provider (e.g. OAuth) can be dropped in without
touching callers.
"""
