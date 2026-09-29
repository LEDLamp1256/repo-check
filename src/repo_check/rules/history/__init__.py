"""History rules: policy over ``RepositorySnapshot.history``.

These rules run only when history analysis was requested (``--history N``).
They read normalized ``HistoryState`` facts and the snapshot's current file
classification, and never run Git, read files, or classify paths.
"""
