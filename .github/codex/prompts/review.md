Review this pull request for correctness, security, code quality, and adherence to
the repository's conventions. The checkout is the pull request merge commit; use
`git diff HEAD^1...HEAD^2` as the primary review scope.

Read `CONTRIBUTING.md` and any guidance relevant to the changed files. For changes
under `integrations/`, also read `integrations/CLAUDE.md` and the closest package
guidance file when one exists.

Focus on actionable defects introduced by this pull request:

- incorrect behavior, regressions, race conditions, and unhandled edge cases;
- security vulnerabilities, exposed secrets, unsafe input handling, or PII in logs;
- missing or inadequate tests for behavior that changed;
- incompatible public API, configuration, packaging, or release changes;
- violations of language-specific checks used by this repository (Ruff/mypy/pytest,
  ESLint/TypeScript/Vitest, or gofmt/vet/golangci-lint/go test).

Do not report purely stylistic preferences, pre-existing issues, or speculative
concerns without a concrete failure mode. Review generated and vendored files only
when they reveal a problem in the source change that produced them.

Organize findings from highest to lowest severity. For each finding, identify the
affected file and line or diff hunk, explain the impact, and give a concise suggested
fix. Label findings as Critical, Warning, or Suggestion. Critical findings are concrete
correctness, security, or regression issues that require changes before merge; Warnings
and Suggestions are non-blocking. If there are no actionable findings, say so
explicitly. Keep the review focused and suitable for posting directly as a GitHub pull
request review.
