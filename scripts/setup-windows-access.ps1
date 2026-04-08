# OpenClaw Monitor — Windows Access Setup
# Run this script once as Administrator in PowerShell.
# It adds the firewall rules for ports 80/443 and the hosts file entry.

$ErrorActionPreference = "Stop"
$domain = "openclaw-frostbite.duckdns.org"
$hostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
$hostsEntry = "127.0.0.1   $domain"

Write-Host "`n[1/3] Adding Windows Firewall rules for ports 80 and 443..." -ForegroundColor Cyan

$ruleName80  = "OpenClaw Monitor HTTP"
$ruleName443 = "OpenClaw Monitor HTTPS"

foreach ($rule in @($ruleName80, $ruleName443)) {
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -DisplayName $rule
    }
}

New-NetFirewallRule -DisplayName $ruleName80 `
    -Direction Inbound -Protocol TCP -LocalPort 80 `
    -Action Allow -Profile Any | Out-Null

New-NetFirewallRule -DisplayName $ruleName443 `
    -Direction Inbound -Protocol TCP -LocalPort 443 `
    -Action Allow -Profile Any | Out-Null

Write-Host "    Firewall rules added." -ForegroundColor Green

# ── Hosts file ──────────────────────────────────────────────────────────────
Write-Host "`n[2/3] Adding hosts file entry ($domain -> 127.0.0.1)..." -ForegroundColor Cyan

$content = Get-Content $hostsFile -Raw
if ($content -notmatch [regex]::Escape($domain)) {
    Add-Content -Path $hostsFile -Value "`n$hostsEntry"
    Write-Host "    Hosts entry added." -ForegroundColor Green
} else {
    # Update existing entry in case it points somewhere else
    $content = $content -replace "(?m)^.*\s+$([regex]::Escape($domain)).*$", $hostsEntry
    Set-Content -Path $hostsFile -Value $content
    Write-Host "    Hosts entry updated (already existed)." -ForegroundColor Green
}

# ── Flush DNS ────────────────────────────────────────────────────────────────
Write-Host "`n[3/3] Flushing DNS cache..." -ForegroundColor Cyan
ipconfig /flushdns | Out-Null
Write-Host "    DNS cache cleared." -ForegroundColor Green

Write-Host "`nDone! Open https://$domain in your browser.`n" -ForegroundColor Green
