# Governance, security, and deployment

Zep applies authorization, retention, and audit across every Context Graph and every query. Relevant when building for regulated or enterprise environments.

## Access control (RBAC) — Enterprise

Roles at two scopes:

- **Account-level:** Account Owner (full control, billing, assigns owners), Account Admin (manage projects, ingest/delete data, assign project roles), Billing Admin (invoices/payments only), Account Viewer (read-only metadata), Project Creator (create projects only).
- **Project-level:** Project Admin (manage project, invite, create API keys, ingest/delete), Project Editor (read/write data; no role/key management), Project Viewer (read-only).

A member can hold multiple assignments. Beyond RBAC, Zep supports attribute-based access control (ABAC) and retention policies (including legal hold).

## Audit logging — Enterprise

Tracks **web dashboard** actions (not API calls — see API logging for those): authentication, member/role changes, API key lifecycle, project changes, access-control changes, data operations (create/delete/view of threads/users/graphs), and settings changes. Each entry records timestamp (ms precision), actor, action, resource, IP, and user agent. Retention: 30 days in dashboard; 1 year cold storage (Enterprise); 7 years with HIPAA BAA.

## Provenance

Every fact traces back to the source episode that produced it — so any answer can be audited to where it came from. This is why episodes are stored verbatim alongside derived knowledge.

## Deployment models

The trust boundary moves with the choice:

1. **Cloud** — Zep-managed. SOC 2 Type II; HIPAA BAA available for Enterprise.
2. **Cloud + BYOK** — Zep runs the service; you control AWS KMS encryption keys (and can revoke).
3. **BYOC** — Zep deployed in your VPC; full network and compliance boundary.

**Bring Your Own LLM (BYOM):** route extraction/generation through your own OpenAI, Anthropic, Google, AWS Bedrock, or Azure account to apply your negotiated pricing and compliance commitments.

## Compliance

SOC 2 Type II certified; HIPAA BAA available. Real-time status at trust.getzep.com.
