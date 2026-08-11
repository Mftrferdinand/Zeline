# Linear CLI

> Manage Linear issues, projects, and cycles from the command line via the `linear` CLI, with git integration.

Use this skill to automate Linear management — creating and updating issues, comments, projects, cycles, and labels — from the terminal.

## Prerequisites

The `linear` command must be available on PATH. Check:

```bash
linear --version
```

If not installed, follow the instructions at:
https://github.com/schpet/linear-cli?tab=readme-ov-file#install

## Best Practices for Markdown Content

When working with issue descriptions or comment bodies that contain markdown, **always prefer file-based flags** instead of passing content as command-line arguments:

- Use `--description-file` for `issue create` and `issue update`
- Use `--body-file` for `comment add` and `comment update`

**Why:**

- Ensures proper formatting in the Linear web UI
- Avoids shell escaping issues with newlines and special characters
- Prevents literal `\n` sequences from appearing in markdown
- Makes multi-line content easier to work with

**Example workflow:**

```bash
# Write markdown to a temporary file
cat > /tmp/description.md <<'EOF'
## Summary

- First item
- Second item

## Details

This is a detailed description with proper formatting.
EOF

# Create issue using the file
linear issue create --title "My Issue" --description-file /tmp/description.md

# Or for comments
linear issue comment add ENG-123 --body-file /tmp/comment.md
```

**Only use inline flags** (`--description`, `--body`) for simple, single-line content.

## Available Commands

```
linear auth               # Manage authentication
linear issue              # Manage issues
linear team               # Manage teams
linear project            # Manage projects
linear project-update     # Manage project status updates
linear cycle              # Manage team cycles
linear milestone          # Manage project milestones
linear initiative         # Manage initiatives
linear initiative-update  # Manage initiative status updates
linear label              # Manage issue labels
linear document           # Manage documents
linear config             # Interactively generate .linear.toml configuration
linear schema             # Print the GraphQL schema to stdout
linear api                # Make a raw GraphQL API request
```

## Discovering Options

Run `--help` on any command to see subcommands and flags:

```bash
linear --help
linear issue --help
linear issue list --help
linear issue create --help
```

## Using the GraphQL API Directly

**Prefer the CLI for all supported operations.** The `api` command is a fallback for queries the CLI does not cover.

### Check the schema for available types and fields

```bash
linear schema -o "${TMPDIR:-/tmp}/linear-schema.graphql"
grep -i "cycle" "${TMPDIR:-/tmp}/linear-schema.graphql"
grep -A 30 "^type Issue " "${TMPDIR:-/tmp}/linear-schema.graphql"
```

### Make a GraphQL request

**Important:** GraphQL queries containing non-null type markers (e.g. `String!`) must be passed via heredoc stdin to avoid escaping issues. Simple queries without those markers can be passed inline.

```bash
# Simple query (inline is fine)
linear api '{ viewer { id name email } }'

# Query with variables — use heredoc
linear api --variable teamId=abc123 <<'GRAPHQL'
query($teamId: String!) { team(id: $teamId) { name } }
GRAPHQL

# Search issues by text
linear api --variable term=onboarding <<'GRAPHQL'
query($term: String!) { searchIssues(term: $term, first: 20) { nodes { identifier title state { name } } } }
GRAPHQL

# Pipe to jq for filtering
linear api '{ issues(first: 5) { nodes { identifier title } } }' | jq '.data.issues.nodes[].title'
```

### Advanced: curl directly

For full HTTP control, use `linear auth token`:

```bash
curl -s -X POST https://api.linear.app/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: $(linear auth token)" \
  -d '{"query": "{ viewer { id } }"}'
```
