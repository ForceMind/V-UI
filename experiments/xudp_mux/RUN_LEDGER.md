# Shared mux experiment run ledger

This isolated experiment does not qualify product UDP. Preserve each failed
attempt; source changes require a new exact-head run rather than retrying for green.

## 2026-10-17 first mechanism run: compile failure

- Commit: `f009350f7c8ad554b8ed86fbc8131bcdadd9bb2f`
- Tree: `83d5456b4017e89b6a7999ddc7dd2dd4d1c110bb`
- [Run 39006786929, attempt 1](https://github.com/ForceMind/V-UI/actions/runs/39006786929): failure.
- Fixed Go 1.25.14 and all 12 Python controls passed.
- Both actual-route `route-only` backpressure controls passed and reproduced
  the expected retained route copy workers (reply: 2; StatusEnd: 1).
- The new mux unit race, unit debug, and actual-route groups failed to build:
  `mux.go` retained an unused `N` network import after removing the direct writer.
  None of those tests executed. The dependent real-runtime matrix was skipped.
- Follow-up change: remove that unused import only from the mux implementation.
  Assertions, limits, source pins, and prior failures remain unchanged.
- [Evidence artifact 12091461961](https://github.com/ForceMind/V-UI/actions/runs/39006786929/artifacts/12091461961):
  ZIP 86,596 bytes, SHA-256
  `161746df3543cf858fdf3df2c1f65b0df810633cd42f3d9a818703c9ab54ed78`.
  All 30 manifest entries were verified by size and SHA-256 after download.
  `VERIFICATION_FAILED.json` correctly rejects the absent executed mux tests.
