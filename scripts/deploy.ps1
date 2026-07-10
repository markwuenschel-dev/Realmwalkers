#!/usr/bin/env pwsh
# One-command deploy of Realmwalkers to the shared AWS box (docs/DEPLOY.md has the full story).
#
#   ./scripts/deploy.ps1                # deploy latest main
#   ./scripts/deploy.ps1 -Ref <sha>     # roll back / deploy a specific commit or tag
#   ./scripts/deploy.ps1 -DryRun        # print the remote script instead of running it
#
# This is exactly the manual loop from DEPLOY.md (ssh -> git pull -> compose rebuild -> logs),
# plus a public-URL health check at the end. A deploy costs nothing per run: the pull and the
# docker build happen on the box (flat EC2 bill), and DNS is not involved.

param(
  [string]$Ref = "main",
  [string]$SshKey = (Join-Path $HOME ".ssh/shared-box.pem"),
  [string]$BoxHost = "ubuntu@44.198.76.44",
  [string]$Url = "https://realmwalkers.44-198-76-44.nip.io",
  [int]$Tail = 40,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# The box's clone is deploy-only (never edited in place), so main can be hard-synced to origin.
# Any other ref — a rollback SHA, a tag — is checked out detached; `git checkout main` restores.
$sync = if ($Ref -eq "main") {
  "git checkout -q main && git reset --hard origin/main"
} else {
  "git checkout -q --detach '$Ref'"
}

$remote = @"
set -eu
cd /opt/stack/Realmwalkers
git fetch -q origin
$sync
echo "deploying `$(git rev-parse --short HEAD): `$(git log -1 --format=%s)"
cd /opt/stack/infra
docker compose up -d --build realmwalkers
docker compose logs --tail=$Tail realmwalkers
"@

if ($DryRun) { Write-Host $remote; exit 0 }

if (-not (Test-Path $SshKey)) { throw "SSH key not found: $SshKey" }
$remote | ssh -i $SshKey $BoxHost "bash -s"
if ($LASTEXITCODE -ne 0) { throw "deploy failed (ssh exit $LASTEXITCODE)" }

# Prove the public URL serves the new build — logs alone don't show what Caddy is fronting.
$code = & curl.exe -fsSL -o NUL -w "%{http_code}" --max-time 30 "$Url/"
if ($LASTEXITCODE -ne 0) { throw "deployed, but the health check against $Url failed" }
Write-Host "$Url -> HTTP $code"
