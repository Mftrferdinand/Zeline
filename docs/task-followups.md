# Task-scoped proactive follow-up

A follow-up is a task-specific monitor registered only for an owner-requested task. It uses the existing scheduler, not a second background service. The monitor checks a bounded source snapshot every ten minutes and sends an owner DM only when the observed value changes from its recorded baseline. With no change, the scheduler tick is silent. A successfully delivered change advances the baseline so the same event is deduplicated. Failed delivery leaves the baseline unchanged for a later retry.

The operator can stop the monitor. Completion, cancellation, expiry, and explicit stop remove the corresponding watch; when no watches remain, the scheduler job is removed. A scheduler that is not active cannot accept new watches. Missing or unreadable sources retain the baseline and can trigger a bounded degraded-monitoring alert, rather than reporting a false change.

A single poll shares one email inbox snapshot across all email filters. It attempts at most two Telegram sends per tick, with a short per-request timeout and one attempt; remaining changes are deferred without advancing their baseline. The processing order rotates between ticks to prevent an unreachable early watch from starving later watches. This limits network time in the child scheduler job, but a burst of more than two changes can take multiple ten-minute ticks to notify fully.

This feature is not active for arbitrary tasks. Registration requires an explicit task and supported source; a source must be a bounded local JSON/text file or a filtered mailbox. The packaged public SOUL remains neutral. An instance-specific private persona, if configured, lives outside the source checkout and wheel.
