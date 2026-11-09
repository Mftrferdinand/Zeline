# Per-instance private persona

The wheel ships the public `zeline/SOUL.md` as the canonical default. Keep personal instructions out of the checkout and public wheel. To customize a single instance, create `$ZELINE_HOME/SOUL.md` (default `~/.zeline/SOUL.md`) with permissions restricted to its owner. Zeline validates the packaged default first and then loads the private file at process import. An empty or unreadable private file fails startup rather than silently reverting to the public persona.

The private file is **not** bundled or uploaded by the package build. Copy and back it up separately when moving an instance; use an isolated `ZELINE_HOME` during package smoke tests. Editing the file does not hot-reload a running gateway: a later, explicitly authorized restart is needed to activate the change. A local source check and a built-wheel check do not prove the installed gateway has loaded the new code.
