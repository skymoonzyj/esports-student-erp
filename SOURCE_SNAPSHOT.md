# Private ERP source snapshot

This snapshot contains the source, tests, migrations and build/deployment configuration of the selected development workspace. It is not a database backup and does not establish production release acceptance.

The original Git history remains in the local project and has not been uploaded. Local databases, attachments, exports, verification screenshots/logs, real environment files and original E2E account values are excluded.

Where the seed/test helpers require `apps/api/app/cli/e2e_credentials.json`, it contains synthetic test-only identifiers and passwords. Phone identifiers retain their validation format and the email-only case uses example.invalid. Never reuse these fixtures for production accounts.

The safe `.env.example` comes from the main ERP workspace. Create a local environment file and supply new deployment secrets. Follow README.md for setup and tests.

GitHub `main` represents the current profile/housing development workspace. `snapshots/nas-release-sync`, `snapshots/local-main`, and `snapshots/assisted-personnel` retain the other source variants separately. They have not been merged into this workspace.
