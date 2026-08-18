# tauri-sign.ps1 — bundle.windows.signCommand for Hermes Setup.
#
# Tauri invokes this once per binary it bundles (the app exe, then the
# NSIS installer itself), so everything inside the artifact ends up
# signed — same coverage electron-builder gives the desktop app.
#
# Auth is the release workflow's workload identity: azure/login has
# already exchanged the GitHub OIDC token, and AZURE_CLIENT_ID /
# AZURE_TENANT_ID / AZURE_FEDERATED_TOKEN_FILE are in the environment,
# which Azure.Identity's credential chain picks up without any secret.
#
# No AZURE_SIGN_ENDPOINT => no-op success: forks and local builds stay
# unsigned but keep working, the same posture as the desktop workflow.
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$FilePath
)

$ErrorActionPreference = 'Stop'

if (-not $env:AZURE_SIGN_ENDPOINT) {
    Write-Host "tauri-sign: AZURE_SIGN_ENDPOINT not set - leaving $FilePath unsigned"
    exit 0
}

if (-not (Get-Module -ListAvailable -Name TrustedSigning)) {
    Install-Module -Name TrustedSigning -Force -Scope CurrentUser -Repository PSGallery
}

Invoke-TrustedSigning `
    -Endpoint $env:AZURE_SIGN_ENDPOINT `
    -CodeSigningAccountName $env:AZURE_SIGN_ACCOUNT `
    -CertificateProfileName $env:AZURE_SIGN_PROFILE `
    -Files $FilePath `
    -FileDigest SHA256 `
    -TimestampRfc3161 'http://timestamp.acs.microsoft.com' `
    -TimestampDigest SHA256

Write-Host "tauri-sign: signed $FilePath"
