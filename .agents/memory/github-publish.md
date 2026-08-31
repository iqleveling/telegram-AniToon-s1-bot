---
name: GitHub publish fallback
description: Publishing imported repositories when the HTTPS Git remote lacks password/token authentication.
---

The imported repository may have a valid GitHub HTTPS remote but reject normal `git push` because password authentication is disabled. When the GitHub integration is attached, publish the complete local change set atomically through the Git data API rather than asking for a token or creating one commit per file.

**Why:** This preserves the user’s requested GitHub-only destination without exposing credentials or leaving a partially updated repository.

**How to apply:** Verify the target branch and current parent commit first, create blobs and one tree/commit, then update the branch ref without force.