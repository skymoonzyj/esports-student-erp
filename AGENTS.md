# NAS Deployment Rule

After completing a user-requested ERP code update and passing the applicable
local checks, run `scripts/deploy-nas.sh` before the final response. Treat a
failed NAS preflight or deployment as a release blocker and report it clearly.

Do not bypass the script with direct file copies or ad hoc Docker commands.
It preserves the existing NAS secrets and data volume, checks the active
Alembic revision before copying code, creates a database backup before a
migration, and does not stop or delete PostgreSQL volumes.

The repository post-commit hook also invokes this script automatically. Set
`ERP_NAS_AUTO_DEPLOY=0` only when a deployment is intentionally paused.
