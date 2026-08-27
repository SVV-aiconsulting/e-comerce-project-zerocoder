param(
    [string]$CertificateUrl = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
    [string]$ExpectedSha256 = "936A43FEA6E8E525BCC0F81ACD9C3D21B4FC4B9B68ACEA7906D698005AFC6504"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$certificateDirectory = Join-Path $projectRoot ".local\certs"
$certificatePath = Join-Path $certificateDirectory "russian_trusted_root_ca_pem.crt"

New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
Invoke-WebRequest -Uri $CertificateUrl -OutFile $certificatePath -SkipCertificateCheck

$certificate = Get-Item -LiteralPath $certificatePath
if ($certificate.Length -lt 1000) {
    throw "Downloaded certificate is unexpectedly small."
}

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $certificatePath).Hash
if ($actualSha256 -ne $ExpectedSha256) {
    Remove-Item -LiteralPath $certificatePath -Force
    throw "Downloaded certificate SHA256 mismatch."
}

Write-Output "GigaChat CA bundle saved: $certificatePath"
Write-Output "SHA256: $actualSha256"
