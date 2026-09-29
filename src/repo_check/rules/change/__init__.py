"""Change rules: policy over ``RepositorySnapshot.change``.

These rules run when change analysis was requested. They read normalized
``ChangedPath`` facts and never run Git, read diffs, or classify paths.
"""
