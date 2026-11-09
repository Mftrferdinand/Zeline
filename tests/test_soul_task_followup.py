"""Keep Zeline's task-scoped proactive follow-up contract explicit."""
from pathlib import Path


def test_documentation_describes_task_scoped_proactive_followup():
    root = Path(__file__).resolve().parents[1]
    soul = (root / "zeline" / "SOUL.md").read_text()
    assert "## Task-scoped proactive follow-up" not in soul
    section = (root / "docs" / "task-followups.md").read_text().lower()
    for required in (
        "existing scheduler", "task-specific", "owner dm", "baseline",
        "deduplicated", "no change", "stop the monitor", "not active",
    ):
        assert required in section
