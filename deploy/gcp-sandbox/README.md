# GCP isolated pytest runner

This deployment creates the execution boundary used by
`GcpCloudRunJobValidationRunner`. It does not deploy the user-facing research API,
apply a proposed patch to the source repository, or grant deployment permission to
the developer agent.

## Runtime flow

1. The controller creates a deterministic ZIP from the temporary `src`, `tests`,
   and `pyproject.toml` workspace.
2. It uploads `workspace.zip` and `request.json` with create-only GCS writes.
3. It starts one Cloud Run Job task with `SANDBOX_REQUEST_JSON` as an execution-only
   environment override.
4. The worker checks the request size and SHA-256, safely extracts only regular files,
   and runs one fixed `python_compile` or `pytest` validation.
5. The worker creates `result.json`. The controller accepts it only when request ID,
   bundle checksum, validation kind, and targets all match.
6. Bucket lifecycle policy removes request objects after one day.

## Security properties

- The worker image must be pinned by digest.
- The worker and controller can only create and get bucket objects. Their custom role
  cannot list, overwrite, or delete objects.
- The Cloud Run Job has one task, zero retries, 1 CPU, 512 MiB memory, and a 180-second
  upper timeout.
- Direct VPC egress routes all traffic through a dedicated `/26` subnet.
- Private DNS sends `*.googleapis.com` to Private Google Access addresses. Egress
  firewall rules allow TCP 443 only to that range and deny other IPv4 egress.
- The pytest subprocess receives a small sanitized environment. No application secret
  is mounted in the worker image or environment.
- A successful check still records `applied_to_source=false` and `deployed=false`.

The service account metadata endpoint remains part of the Cloud Run runtime. Its token
therefore has only the custom two-permission object role on a dedicated, short-lived
bucket. For stronger tenant isolation later, replace bucket credentials with
per-request signed URLs and a credential-free worker identity.

## Build and deploy

Build the image from the repository root and push it to an existing private Artifact
Registry repository:

```bash
docker build \
  -f deploy/gcp-sandbox/Dockerfile \
  -t asia-northeast3-docker.pkg.dev/PROJECT/REPOSITORY/sandbox-worker:VERSION \
  .
docker push \
  asia-northeast3-docker.pkg.dev/PROJECT/REPOSITORY/sandbox-worker:VERSION
```

Resolve the pushed digest and use the `@sha256:...` image reference in a local
`terraform.tfvars` copied from `terraform.tfvars.example`. Do not commit that local
file. Then review and apply:

```bash
cd deploy/gcp-sandbox
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

The caller applying Terraform needs permission to enable APIs and create the listed
IAM, Storage, Compute, DNS, and Cloud Run resources. The runtime controller principal
receives only the custom bucket role and `roles/run.jobsExecutorWithOverrides` on this
single job.

## Controller wiring

Use Application Default Credentials in the deployed controller:

```python
from pathlib import Path

from defense_research_agent.services.code_sandbox import CodeSandboxExecutor
from defense_research_agent.services.gcp_code_sandbox import (
    GcpCloudRunJobValidationRunner,
    GcpCloudRunSandboxJobExecutor,
    GcsSandboxObjectStore,
)

remote_validation = GcpCloudRunJobValidationRunner(
    GcsSandboxObjectStore("YOUR_BUCKET"),
    GcpCloudRunSandboxJobExecutor(
        project_id="YOUR_PROJECT",
        region="asia-northeast3",
        job_name="defense-research-sandbox",
    ),
)
code_sandbox = CodeSandboxExecutor(
    source_root=Path("/app"),
    sandbox_root=Path("/tmp/defense-research-sandboxes"),
    validation_runner=remote_validation,
)
```

No GCP resource is created by the Python code. Terraform deployment, image promotion,
and controller configuration remain explicit operator actions.
