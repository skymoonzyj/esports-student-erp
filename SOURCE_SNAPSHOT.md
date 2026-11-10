# Private ERP source snapshot

This is a sanitized source snapshot of the current profile/housing ERP workspace,
including the completed PC MOBA v5 update and previously deployed changes.
The original Git history, operational data, real environment files, credentials,
verification logs, screenshots, databases and attachments are not included.

The E2E credential fixture is the previously published synthetic test-only fixture.
Never reuse its accounts or passwords in production. The safe `.env.example` is
also preserved from the previously published snapshot; supply new local secrets.

`SOURCE_PROVENANCE.json` and `SOURCE_MANIFEST.sha256` identify this dirty-worktree
snapshot. The source Git revision alone does not identify its full contents.
This publication does not run a deployment. Refer to the supplied release metadata
and verification report for the separately completed NAS release.
