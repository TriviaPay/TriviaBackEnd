# OCI Instance Pool Deployment (ARM)

This repo includes Terraform and GitHub Actions support for deploying the API on OCI ARM compute instance pools with autoscaling and a load balancer.

## What Gets Provisioned (Terraform)
- VCN, public/private subnets, Internet/NAT/Service gateways, route tables
- NSGs for LB and API
- OCI Load Balancer + backend set + listener
- OCIR repo
- Vault + key + secret containers (app env, OCIR username, OCIR auth token)
- Instance configuration + instance pool
- Autoscaling policy (CPU-based)
- Logging log group + custom log + unified agent configuration (app log tailing)

## Prereqs
- OCI tenancy + compartment
- OCIR auth token (for docker pulls)
- Terraform state backend (OCI Object Storage)
- OCI Logging destination (for Grafana dashboards)

## Terraform Remote State (OCI Object Storage)
Create a bucket (versioning + KMS recommended), then update `infra/backend.tf`:
```bash
oci os bucket create \
  --compartment-id <COMPARTMENT_OCID> \
  --name <STATE_BUCKET> \
  --namespace <NAMESPACE> \
  --versioning Enabled
```

Edit `infra/backend.tf`:
```
bucket           = "<STATE_BUCKET>"
namespace        = "<NAMESPACE>"
region           = "<REGION>"
compartment_ocid = "<COMPARTMENT_OCID>"
key              = "triviapay/terraform.tfstate"
```

## Values to Fill from OCI
- Tenancy OCID
- Compartment OCID
- Compartment name
- Region
- Availability domain (optional; leave empty to use AD[0])
- OCIR namespace + registry + repo
- Instance image OCID (ARM shape image)
- Load balancer ingress CIDRs (Cloudflare IP ranges)
- State backend: bucket + namespace + region + compartment OCID
- Healthcheck URL (post-deploy smoke test)

## Terraform Usage
```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars
terraform init
terraform apply
```

Terraform outputs (consumed automatically by GitHub Actions):
- `backend_port`
- `instance_pool_id`
- `private_subnet_id`
- `api_nsg_id`
- `lb_id`
- `lb_backend_set_name`
- `app_env_secret_id`
- `ocir_username_secret_id`
- `ocir_auth_token_secret_id`
- `availability_domain`
- `compartment_ocid`
- `instance_image_id`
- `instance_shape`
- `instance_ocpus`
- `instance_memory_gbs`
- `ocir_namespace`
- `ocir_registry`
- `ocir_repo_name`
- `ocir_repo_url`
- `uma_configuration_id`
- `app_log_id`

These output names are referenced directly by the deploy workflow; if you rename them, update `.github/workflows/oci-deploy.yml` accordingly.

## GitHub Actions (OCI Deploy) — Workload Identity Federation
This workflow uses GitHub OIDC to obtain short-lived OCI tokens (no long-lived API keys).
It reads Terraform outputs from the OCI Object Storage backend at deploy time, so no OCI IDs or ports are manually copied into GitHub.
Concurrency is enforced per environment (`oci-<env>-deploy`) to prevent overlapping deploys from racing against the same state.
Deploys are guarded to the canonical repo and allow: prod = main only; staging = main/staging/tags.

Required variables/secrets:
- `OCI_WIF_DOMAIN_BASE_URL` (Identity Domain base URL)
- `OCI_WIF_AUDIENCE` (OIDC audience; default `https://github.com/<org>/<repo>`)
- `OCI_WIF_CLIENT_IDENTIFIER` (optional; public identifier only — no client secret stored in GitHub)
- `OCI_CANONICAL_REPO` (GitHub `org/repo` for repo guard)
- `OCI_TENANCY_OCID`
- `OCI_REGION`
- `OCI_STATE_BUCKET`
- `OCI_STATE_NAMESPACE`
- `OCI_STATE_REGION`
- `OCI_STATE_COMPARTMENT_OCID`
- `OCI_LB_WAIT_TIMEOUT_SECONDS` (optional)
- `OCI_BACKEND_DRAIN_TIMEOUT_SECONDS` (optional; default `120`)
- `HEALTHCHECK_URL` (optional post-deploy smoke test)
- `HEALTHCHECK_DB_URL` (optional post-deploy DB check)
- `OCI_TF_DRIFT_CHECK` (optional; set to `true` to run plan drift warnings)

All OCI IDs, ports, and secret OCIDs are pulled from Terraform outputs at deploy time (no manual copy/paste into GitHub).
The deploy workflow fails fast if required Terraform outputs are missing; run `terraform apply` first.

## WIF Trust Policy (Secure Defaults)
Restrict by repo, branch, and audience:
```
issuer: https://token.actions.githubusercontent.com
audience: <OCI_WIF_AUDIENCE>
subject: repo:<org>/<repo>:ref:refs/heads/main
```
Adjust the subject to match your environment or protected branches.
Minimum trust checklist:
- repo pinned
- branch pinned (e.g., `refs/heads/main`)
- audience pinned
- environment pinned if you use GitHub Environments

## WIF/OIDC Setup (High-Level)
1) Create an Identity Domain OIDC provider for GitHub (`https://token.actions.githubusercontent.com`).
2) Create a Workload Identity Federation trust with conditions on:
   - `aud` = `<OCI_WIF_AUDIENCE>`
   - `sub` = `repo:<org>/<repo>:ref:refs/heads/main`
3) Map the trust to a dynamic group and attach IAM policies for:
   - instance pool updates
   - instance configuration creation
   - reading Vault secret bundles (for OCIR creds)
   - log group / log configuration updates (if needed)
   - OCIR repository read/write

No client secret is stored in GitHub; if an identity domain requires a client identifier, it is a public identifier only.

Example policy statements (replace placeholders):
```
Allow dynamic-group <WIF_DYNAMIC_GROUP> to manage instance-configurations in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to manage instance-pools in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to read secret-bundles in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to use log-groups in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to use log-content in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to read repos in compartment <COMPARTMENT_NAME>
Allow dynamic-group <WIF_DYNAMIC_GROUP> to manage repos in compartment <COMPARTMENT_NAME>
```

## Secret Injection (Out-of-Band)
Terraform creates secret containers with placeholder content (`CHANGEME`) so real secret payloads never land in state. Inject real values after apply:
The deploy workflow reads OCIR credentials from Vault at runtime (no OCIR username/auth token stored in GitHub).
```bash
# App env (base64 encoded)
base64 -i /path/to/.env.production | tr -d '\\n' > /tmp/app_env.b64
oci vault secret update --secret-id <APP_ENV_SECRET_ID> \\
  --secret-content \"{\\\"content-type\\\":\\\"BASE64\\\",\\\"content\\\":\\\"$(cat /tmp/app_env.b64)\\\"}\"

# OCIR username and auth token
echo -n \"<OCIR_USERNAME>\" | base64 | tr -d '\\n' > /tmp/ocir_user.b64
echo -n \"<OCIR_AUTH_TOKEN>\" | base64 | tr -d '\\n' > /tmp/ocir_token.b64
oci vault secret update --secret-id <OCIR_USERNAME_SECRET_ID> \\
  --secret-content \"{\\\"content-type\\\":\\\"BASE64\\\",\\\"content\\\":\\\"$(cat /tmp/ocir_user.b64)\\\"}\"
oci vault secret update --secret-id <OCIR_AUTH_TOKEN_SECRET_ID> \\
  --secret-content \"{\\\"content-type\\\":\\\"BASE64\\\",\\\"content\\\":\\\"$(cat /tmp/ocir_token.b64)\\\"}\"
```

## Cloudflare Fronting
- Set `lb_ingress_cidrs` to Cloudflare IP ranges in `terraform.tfvars`.
- Keep the LB listener on HTTP if Cloudflare terminates TLS.

## Rolling Updates
The `oci-deploy` workflow creates a new instance configuration, updates the pool, then uses surge updates (min=1) to avoid downtime while waiting for LB backend health:
```bash
infra/scripts/rolling_update.sh <POOL_ID> <COMPARTMENT_OCID> 60 1
```
Optional LB health gating + connection draining:
```bash
LB_ID=<LB_ID> BACKEND_SET_NAME=api-backend BACKEND_PORT=8000 \
BACKEND_DRAIN_TIMEOUT_SECONDS=120 MAX_WAIT_SECONDS=1800 VNIC_SUBNET_ID=<SUBNET_OCID> \
  infra/scripts/rolling_update.sh <POOL_ID> <COMPARTMENT_OCID> 60 1
```
This scales to 2, waits for new backend members (by instance private IP) to be healthy, drains old backends, terminates old instances, then scales back to 1.
Ensure `BACKEND_PORT` matches the LB backend port.
`VNIC_SUBNET_ID` is optional; when set, the script selects the instance VNIC in that subnet and fails the rollout if no match is found.
LB health checks are configured for `GET /health` with return code `200` in `infra/lb.tf`; update that file if your readiness endpoint differs.
Port consistency: the app listens on `PORT` (default 8000); cloud-init sets `PORT=${backend_port}` if missing; the LB backend set uses `backend_port`; and the rolling update script uses `BACKEND_PORT`. Keep these aligned.
Rollout logs print the selected VNIC/subnet/IP for each instance to help validate backend mapping.
Port drift guard: treat Terraform `backend_port` as the source of truth. If you change it, re-apply Terraform and update any Vault-provided `PORT` overrides. The workflow reads the port from Terraform outputs.
Production recommendation: keep host port == container port == `backend_port` so LB backend port and the container listener are always the same.

Rollback (one step):
```bash
infra/scripts/rollback.sh <POOL_ID> <COMPARTMENT_OCID> <PREVIOUS_INSTANCE_CONFIG_ID> 60 1
```
The previous instance config ID is logged in the `oci-deploy` workflow summary (or fetch it with `oci compute-management instance-pool get`).

## Post-deploy Smoke Test
Set `HEALTHCHECK_URL` in GitHub Secrets to enable the cache-bypassing smoke test after the rolling update. For a deeper check (e.g., DB connectivity), set `HEALTHCHECK_DB_URL` to an endpoint that performs a DB call.

## Logging + Grafana
Terraform creates an OCI Logging log group, LB access log, and UMA configuration to tail app logs:
- Default path: `/var/log/triviapay/app.log` (cloud-init sets `APP_LOG_PATH` and mounts `/var/log/triviapay`)
- Alternate path: `/var/lib/docker/containers/*/*-json.log`
- UMA is enabled via the Custom Logs Monitoring plugin in the instance configuration.
Host path for `APP_LOG_PATH` is mounted into the container at the same path; cloud-init sets ownership to UID/GID `10001` to match the container user, and UMA (root) can read it after reboot.
Application logging writes to both stdout and `APP_LOG_PATH` via `core/logging.py` using a `WatchedFileHandler` when `APP_LOG_PATH` is set.
Cloud-init installs a logrotate policy that renames the file and creates a new one; `WatchedFileHandler` reopens the new file on the next emit, and UMA tails the same path across rotations.
Logrotate uses the `APP_LOG_PATH` value from `/etc/triviapay.env`, so custom paths are rotated as well.
Override the log paths using `log_source_paths` in `terraform.tfvars`.
Override `APP_LOG_PATH` in the Vault env secret if you want a different file or to disable file logging.
Point Grafana to the OCI Logging data source and query the custom log.
If you use WebSockets/SSE, consider LB idle timeout tuning and longer `BACKEND_DRAIN_TIMEOUT_SECONDS` to avoid disconnects during drain.
If UMA cannot read rotated logs, confirm the agent runs as root; otherwise change logrotate `create` mode to `0644` or add UMA to the log group.

## OCIR Auth Token Rotation
- Create a new OCIR auth token in OCI IAM.
- Update the Vault secret container for `ocir_auth_token` (and `ocir_username` if needed).
- Trigger a deploy so instances pick up the new token.

## Infra CI Checks
`.github/workflows/infra-ci.yml` runs:
- `terraform fmt -check`
- `terraform validate` (with `-backend=false`)
- `tflint` (lint)
- `tfsec` (security)
- `checkov` (security)
- `terraform plan` (on `main`, using WIF + remote state)
Plan output is written to a file (not printed) and scanned to ensure only placeholder secret content exists.

Plan job inputs (GitHub Secrets):
- `OCI_WIF_DOMAIN_BASE_URL`
- `OCI_WIF_AUDIENCE`
- `OCI_WIF_CLIENT_IDENTIFIER` (optional)
- `OCI_TENANCY_OCID`
- `OCI_REGION`
- `OCI_COMPARTMENT_OCID`
- `OCI_COMPARTMENT_NAME`
- `OCI_INSTANCE_IMAGE_ID`
- `OCI_OCIR_NAMESPACE`
- `OCI_OCIR_REPO`
- `OCI_OCIR_REGISTRY`
- `OCI_STATE_BUCKET`
- `OCI_STATE_NAMESPACE`
- `OCI_STATE_REGION`
- `OCI_STATE_COMPARTMENT_OCID`

## Verification Commands (Copy/Paste)
Remote state backend + versioning:
```bash
terraform -chdir=infra init \
  -backend-config="bucket=<STATE_BUCKET>" \
  -backend-config="namespace=<NAMESPACE>" \
  -backend-config="region=<REGION>" \
  -backend-config="compartment_ocid=<COMPARTMENT_OCID>"

oci os bucket get --name <STATE_BUCKET> --namespace <NAMESPACE> \
  --query "data.versioning" --raw-output

oci os object list --bucket-name <STATE_BUCKET> --namespace <NAMESPACE> \
  --prefix triviapay/terraform.tfstate
```

State contains no real secrets:
```bash
terraform -chdir=infra state pull | rg -n "CHANGEME|secret_content|content"
```

WIF trust policy locked down (no wildcards):
- In Identity Domains, confirm the trust conditions:
  - `aud` = `<OCI_WIF_AUDIENCE>`
  - `sub` = `repo:<org>/<repo>:ref:refs/heads/main`

GitHub Actions OIDC auth (no API keys):
- Check the `Verify OCI auth` step in `oci-deploy` logs. It should print the namespace from `oci os ns get`.

Backend port + IP sanity check (if rollout stalls):
```bash
oci lb backend list --load-balancer-id <LB_ID> --backend-set-name api-backend \
  --query "data[].name" --raw-output

oci compute-management instance-pool list-instances \
  --instance-pool-id <POOL_ID> \
  --compartment-id <COMPARTMENT_OCID> \
  --query "data[].id" --raw-output

oci compute instance list-vnics --instance-id <INSTANCE_ID> \
  --query "data[].{ip:\"private-ip\",subnet:\"subnet-id\",primary:\"is-primary\"}"
```
Compare the `ip:port` from LB backends against the instance primary private IP and confirm `BACKEND_PORT` matches the LB backend port and app listen port.

Instance boots and serves traffic:
```bash
oci compute-management instance-pool list-instances \
  --instance-pool-id <POOL_ID> \
  --compartment-id <COMPARTMENT_OCID> \
  --query "data[?\"lifecycle-state\"=='RUNNING']"

oci lb backend-set-health get \
  --load-balancer-id <LB_ID> \
  --backend-set-name api-backend
```

Rolling update (surge to 2, then back to 1):
- Watch `oci-deploy` logs for pool resize and health gating, or run:
```bash
LB_ID=<LB_ID> BACKEND_SET_NAME=api-backend MAX_WAIT_SECONDS=1800 \
  infra/scripts/rolling_update.sh <POOL_ID> <COMPARTMENT_OCID> 60 1
```

UMA log shipping:
```bash
oci logging search --search-query 'search "<LOG_GROUP_OCID>" | where logId = "<APP_LOG_ID>" | sort by datetime desc' \
  --time-start "2024-01-01T00:00:00Z"
```

Log file + rotation on instance:
```bash
ls -l /var/log/triviapay/app.log
sudo logrotate -f /etc/logrotate.d/triviapay
ls -l /var/log/triviapay
```
