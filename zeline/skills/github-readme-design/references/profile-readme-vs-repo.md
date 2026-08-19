# Profile README vs repository README — what shows what

Users conflate these. Get the distinction straight before building anything.

## Special profile repo (`username/username`)
- Repo name MUST be **exactly** the GitHub username (e.g. `Mftrferdinand/Mftrferdinand`).
- MUST be **public** — private = the README does not render on the profile at all,
  and any image/GIF referenced from it is not publicly reachable.
- Renders the root `README.md` at the top of `github.com/<username>`.
- Shows ONLY the README. No stars / forks / language bar on that card.
- Order on the profile page: avatar + bio + account info → **Profile README** →
  pinned repos → contribution graph.

## Ordinary repository (e.g. `Zerolinear`)
- Its repo page shows the README **plus** ⭐ stars, 🍴 forks, and the language
  breakdown automatically — all on one page. Nothing extra to configure.
- This is what a user usually means by "README but still show star/fork/language".

## Bio field
- Plain short text only. **No** Markdown images, GIFs, HTML, badges, headings, or
  layout. Use it for a one-line summary; put all visual richness in a README.

## Creating the profile repo (gh)
```
gh repo create <username>/<username> --public --source . --remote origin --push
```
Then read it back: `gh api repos/<u>/<u>/readme` and
`gh api repos/<u>/<u>/contents/assets/<file>` to confirm both README and asset
published. Deleting is instant: `gh repo delete <u>/<u> --yes` (needs
`delete_repo` token scope), then verify the view 404s.
