# V-UI development contract

Read ROADMAP.md and the latest docs/ITERATIONS.md before editing. The user has now authorized sequential execution of all scheduled stages, alpha.3 through rc.2. This supersedes the prior instruction to stop after each turn, but not the small-version scope or safety gates.

- Implement and test one stage before proceeding. Use a separate dependent branch/PR for each stage; keep dependencies explicit.
- Do not merge master, the older dependent PRs, publish a Release, or deploy the user's VPS without explicit deployment approval.
- Preserve existing data. Test only with temporary directories and synthetic credentials.
- Do not log tokens, return server secrets in exports, or silently downgrade failed configurations to direct traffic.
- Fixed-version real-core checks are mandatory for protocol claims; unit tests are not connectivity evidence.
- ToClash remains an explicit alpha.5/alpha.6 goal. Do not add optional candidate-pool features to this run.
- Report tested, untested and blocked items separately. A queued CI is not a passed CI.

Current final stage is rc.2. Only the selected Ubuntu24.04 amd64/CPython3.12 deployment is accepted. Finish and verify the final head, then stop; never silently turn the optional queue into more scope. Build-time packages must retain exact pins/manifest/selected licenses; no font files or secrets in artifacts.
