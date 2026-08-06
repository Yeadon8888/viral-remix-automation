# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- Create, read, update, label, comment on, and close tickets with `gh issue` commands from this repository clone.
- Infer the repository from `git remote -v`; the configured remote is `Yeadon8888/viral-remix-automation`.
- Use a heredoc for multi-line issue bodies so specifications and acceptance criteria remain readable.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Skill routing

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", read the GitHub issue and its comments.
- Represent blocking edges with GitHub native issue dependencies when available. Otherwise add a `Blocked by: #...` line to the dependent issue body.
