# Tum projelerde ayni YakupAkn code-signing sertifikasi ile imzala.
# Kullanim:
#   .\GuardImzala.ps1 -Exe .\dist\GuardSecurity.exe
#   .\GuardImzala.ps1 -Exe .\dist\GuardKurulum.exe

param(
  [Parameter(Mandatory = $true)]
  [string]$Exe
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) { throw "Dosya yok: $Exe" }

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
  Where-Object { $_.Subject -match "YakupAkn" } |
  Select-Object -First 1

if (-not $cert) {
  throw "Cert:\CurrentUser\My icinde Subject'inde YakupAkn gecen code-signing sertifikasi yok."
}

Write-Host "Sertifika: $($cert.Subject)"
Write-Host "Thumbprint: $($cert.Thumbprint)"

$sig = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert `
  -TimestampServer "http://timestamp.digicert.com" `
  -HashAlgorithm SHA256

$sig | Format-List Path, Status, StatusMessage, SignerCertificate
if ($sig.Status -notin @("Valid", "UnknownError")) {
  # UnknownError self-signed'da yaygin; imza yine yazilmis olabilir
  Write-Host "Uyari: Status=$($sig.Status) — Properties > Digital Signatures kontrol et."
}
