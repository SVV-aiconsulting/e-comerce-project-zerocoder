# One-time setup: add Apernova SSH public key to VPS authorized_keys.
# Run from PowerShell; enter root password when prompted (once).

$ErrorActionPreference = "Stop"

$hostAlias = "Apernova"
$pubKeyPath = Join-Path $env:USERPROFILE ".ssh\apernova.pub"

if (-not (Test-Path $pubKeyPath)) {
    Write-Error "Public key not found: $pubKeyPath"
}

$pubKey = (Get-Content $pubKeyPath -Raw).Trim()
Write-Host "Installing Apernova key:"
ssh-keygen -lf $pubKeyPath
Write-Host ""
Write-Host "Connecting to $hostAlias as root. Enter VPS root password when prompted."
Write-Host ""

$remoteCmd = @"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -qxF '$pubKey' ~/.ssh/authorized_keys 2>/dev/null || echo '$pubKey' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo 'Apernova key installed successfully.'
"@

ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no $hostAlias $remoteCmd

Write-Host ""
Write-Host "Testing key-based login..."
ssh -o BatchMode=yes $hostAlias "echo SSH key auth OK; whoami; hostname"
