## What was wrong

<!-- The behaviour before this change, and why it is a problem for a user.
     For a new capability: what was impossible or awkward without it. -->

## What changed

<!-- The approach, and why this one rather than the obvious alternative.
     A reviewer reading only this section should be able to predict the diff. -->

## How it was verified

<!-- Real commands and real results, not intentions. -->

```
python -m unittest discover -s tests
ruff check zeline tests
```

- [ ] The full suite passes locally
- [ ] `ruff check zeline tests` is clean
- [ ] A test fails before this change and passes after it
- [ ] No credentials, tokens, real chat IDs, or personal data in the diff
- [ ] No version bump (releases are cut separately)

## Not verified

<!-- Anything you could not exercise here — a platform you have no access to, a
     failure that needs a live service. Say so plainly instead of leaving a
     reviewer to assume it was covered. Delete this section if it is empty. -->
