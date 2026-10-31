<#
.SYNOPSIS
  One-time setup on the gaming desktop so core-01 can pull WoW SavedVariables over SSH (Loop Planner, option 1).

.DESCRIPTION
  Installs the Windows OpenSSH Server feature, starts it on boot, opens TCP 22 on the Private profile only,
  and authorises core-01's public key for this user. Re-runnable; each step is skipped if already done.
  Nothing in the WoW folder is touched.

  Run from an elevated PowerShell:
    Set-ExecutionPolicy -Scope Process Bypass -Force
    C:\scripts\Enable-SvPull.ps1 -CoreKey "ssh-ed25519 AAAA... redmon@core-01"

  Get the key string on core-01 with:  cat ~/.ssh/id_ed25519.pub   (make one first if missing: ssh-keygen -t ed25519)

.PARAMETER CoreKey
  core-01's public key line, verbatim.
.PARAMETER LanCidr
  Restrict the firewall rule to your LAN (e.g. 192.168.1.0/24). Default: any address on the Private profile.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CoreKey,
    [string]$LanCidr = 'Any'
)
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')) {
    throw 'Run this from an elevated PowerShell.'
}
if ($CoreKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]+') { throw 'CoreKey does not look like a public key line.' }

# 1. OpenSSH Server feature
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
if ($cap.State -ne 'Installed') { Write-Host '[1/4] installing OpenSSH Server'; Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
else { Write-Host '[1/4] OpenSSH Server already installed' }

# 2. service on boot
Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne 'Running') { Start-Service sshd }
Write-Host '[2/4] sshd running, startup Automatic'

# 3. firewall: Private profile only (the feature's own rule is all profiles; replace it)
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if (-not (Get-NetFirewallRule -Name 'LoopPlanner-sshd-Private' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'LoopPlanner-sshd-Private' -DisplayName 'OpenSSH Server (Private, Loop Planner pull)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Private -RemoteAddress $LanCidr | Out-Null
}
Write-Host "[3/4] firewall: TCP 22 inbound, Private profile, remote $LanCidr"

# 4. authorised key. Admin accounts use the machine-wide file; others the per-user file.
$isAdminUser = (whoami /groups) -match 'S-1-5-32-544'
if ($isAdminUser) {
    $authFile = 'C:\ProgramData\ssh\administrators_authorized_keys'
} else {
    $authFile = Join-Path $HOME '.ssh\authorized_keys'
    New-Item -ItemType Directory -Force (Split-Path $authFile) | Out-Null
}
$existing = if (Test-Path $authFile) { Get-Content $authFile } else { @() }
if ($existing -notcontains $CoreKey) { Add-Content -Path $authFile -Value $CoreKey -Encoding ascii }
if ($isAdminUser) {
    # OpenSSH refuses the admin keys file unless only SYSTEM and Administrators can touch it
    icacls $authFile /inheritance:r /grant 'SYSTEM:F' /grant 'BUILTIN\Administrators:F' | Out-Null
}
Write-Host "[4/4] key authorised in $authFile"

# default shell for incoming sessions: PowerShell (scp works either way; this just makes `ssh desktop` pleasant)
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -PropertyType String -Force `
    -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' | Out-Null
Restart-Service sshd

Write-Host ''
Write-Host "Done. From core-01, test with:  ssh $env:USERNAME@$env:COMPUTERNAME 'echo ok'"
Write-Host 'Then find the SavedVariables folder (paste this path into the build chat):'
Write-Host '  Get-ChildItem "C:\Program Files (x86)\World of Warcraft\*\WTF\Account\*\SavedVariables\*.lua" | Select-Object FullName'
