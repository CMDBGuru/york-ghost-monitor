# One-command setup for the York Ghost Merchants monitor.
# Run from PowerShell (or via: powershell -NoProfile -ExecutionPolicy Bypass -File "...\setup.ps1")
# Your phone number and App Password are typed into THIS terminal and sent
# straight to GitHub's encrypted secrets -- never displayed, never logged.

# NOTE: native tools (gh/git) print progress to stderr; do NOT treat that as fatal.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
function Fail($m){ Write-Host "`nERROR: $m" -ForegroundColor Red; exit 1 }
Write-Host "=== York Ghost Monitor setup ===" -ForegroundColor Cyan

# 1. GitHub CLI present?
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "Installing GitHub CLI (gh)..." -ForegroundColor Yellow
    winget install --id GitHub.cli -e --source winget --accept-source-agreements --accept-package-agreements
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Host "gh installed. Close this window, open a NEW PowerShell, and run the command again." -ForegroundColor Yellow
        exit 0
    }
}

# 2. Authenticate (opens a browser). gh writes status to stderr, so discard it and check the exit code.
gh auth status 2>$null 1>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Opening GitHub login in your browser -- sign in and approve..." -ForegroundColor Cyan
    gh auth login --web --hostname github.com
    gh auth status 2>$null 1>$null
    if ($LASTEXITCODE -ne 0) { Fail "GitHub login didn't complete. Re-run the command and finish the browser login." }
}
Write-Host "GitHub login OK." -ForegroundColor Green

# 3. Create the private repo + push (or just push if a remote already exists)
git branch -M main 2>$null
$remotes = @(git remote 2>$null)
if ($remotes -notcontains "origin") {
    Write-Host "Creating private repo 'york-ghost-monitor' and pushing code..." -ForegroundColor Cyan
    gh repo create york-ghost-monitor --private --source . --remote origin --push
    if ($LASTEXITCODE -ne 0) { Fail "Repo create/push failed (see the message above). If the repo already exists, tell Claude and it'll adjust." }
} else {
    Write-Host "Pushing to existing remote..." -ForegroundColor Cyan
    git push -u origin main
    if ($LASTEXITCODE -ne 0) { Fail "Push failed (see message above)." }
}
Write-Host "Code pushed." -ForegroundColor Green

# 4. Secrets (values piped straight to GitHub; stderr suppressed so output stays clean)
Write-Host "`nSetting mail secrets..." -ForegroundColor Cyan
$gmail = Read-Host "Your Gmail address (used to send AND receive the alerts)"
$gmail = $gmail.Trim()
$gmail | gh secret set SMTP_USER 2>$null;     Write-Host "  set SMTP_USER"
$gmail | gh secret set MAIL_TO_EMAIL 2>$null; Write-Host "  set MAIL_TO_EMAIL"

$phone = Read-Host "`nYour 10-digit AT&T mobile number (digits only)"
$phone = ($phone -replace '\D','')
"$phone@txt.att.net" | gh secret set MAIL_TO_SMS 2>$null
Write-Host "  set MAIL_TO_SMS -> texts will go to $phone@txt.att.net"

Write-Host "`nGet a 16-char Gmail App Password here (opens in browser):" -ForegroundColor Yellow
Write-Host "  https://myaccount.google.com/apppasswords"
$sec  = Read-Host "Paste the Gmail App Password (hidden as you type)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$pw   = ([Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) -replace '\s','')
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
if ([string]::IsNullOrWhiteSpace($pw)) { Fail "No App Password entered." }
$pw | gh secret set SMTP_PASS 2>$null; Write-Host "  set SMTP_PASS"
$pw = $null

# 5. Fire a test alert
Write-Host "`nTriggering a test alert (text + email)..." -ForegroundColor Cyan
Start-Sleep -Seconds 6
gh workflow run "monitor.yml" -f send_test=true 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Couldn't auto-trigger yet (Actions can take a minute to register a new workflow)." -ForegroundColor Yellow
    Write-Host "Go to the repo's Actions tab -> York Ghost Monitor -> Run workflow -> tick send_test -> Run." -ForegroundColor Yellow
} else {
    Write-Host "Test triggered! Check your phone + email within about a minute." -ForegroundColor Green
}
Write-Host "`n=== Setup complete. The monitor now checks every 5 minutes automatically. ===" -ForegroundColor Green
