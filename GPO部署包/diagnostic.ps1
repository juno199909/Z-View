$ErrorActionPreference = "Continue"

$LogPath = "C:\Windows\Temp\z-view-diagnostic.log"
$InstallDir = "C:\Program Files\CMDB-Agent"
$DataDir = "C:\ProgramData\CMDB-Agent"
$RuntimeDir = Join-Path $DataDir "runtime"
$RuntimeLog = Join-Path $DataDir "logs\agent-runtime.log"
$DeployLog = "C:\Windows\Temp\cmdb-agent-deploy.log"
$PackageDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Diag {
    param([string]$Message = "")
    Write-Host $Message
    Add-Content -LiteralPath $LogPath -Value $Message -Encoding UTF8
}

function Write-Section {
    param([string]$Name)
    Write-Diag ""
    Write-Diag "[$Name]"
}

function Test-CommandResult {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    Write-Diag ""
    Write-Diag "Testing: $Name"
    try {
        $result = & $Command
        if ($null -ne $result -and "$result".Trim()) {
            Write-Diag "$result"
        }
        Write-Diag "[OK] $Name"
    } catch {
        Write-Diag "[FAIL] $Name"
        Write-Diag ("       " + $_.Exception.Message)
    }
}

function Get-ActiveConsoleSessionId {
    try {
        Add-Type -Namespace Win32 -Name Native -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint WTSGetActiveConsoleSessionId();
"@ -ErrorAction SilentlyContinue | Out-Null
        return [Win32.Native]::WTSGetActiveConsoleSessionId()
    } catch {
        return "unknown"
    }
}

function Get-Sha256 {
    param([string]$Path)

    try {
        if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
            return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash
        }

        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $sha = [System.Security.Cryptography.SHA256]::Create()
            $bytes = $sha.ComputeHash($stream)
            return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "").ToUpperInvariant()
        } finally {
            $stream.Dispose()
        }
    } catch {
        return "unavailable: " + $_.Exception.Message
    }
}

function Get-CatalogSignatureStatus {
    param([string]$Root)

    try {
        $cat = @(Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -ieq ".cat" } |
            Select-Object -First 1)
        if (-not $cat) {
            return "missing"
        }
        return [string](Get-AuthenticodeSignature -LiteralPath $cat.FullName).Status
    } catch {
        return "unknown"
    }
}

function Get-VirtualDisplayPayloadState {
    param([string]$Root)

    $state = [ordered]@{
        root = $Root
        exists = $false
        file_count = 0
        has_manifest = $false
        has_manifest_example = $false
        has_inf = $false
        has_cat = $false
        has_sys = $false
        has_complete_payload = $false
        catalog_signature_status = "missing"
        has_trusted_signature = $false
        has_real_payload = $false
        status = "missing"
    }

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return [pscustomobject]$state
    }

    $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue)
    $placeholderNames = @("README.md", "driver_manifest.json.example")
    $nonPlaceholderFiles = @($files | Where-Object { $placeholderNames -notcontains $_.Name })

    $state.exists = $true
    $state.file_count = $files.Count
    $state.has_manifest = Test-Path -LiteralPath (Join-Path $Root "driver_manifest.json") -PathType Leaf
    $state.has_manifest_example = Test-Path -LiteralPath (Join-Path $Root "driver_manifest.json.example") -PathType Leaf
    $state.has_inf = [bool](@($files | Where-Object { $_.Extension -ieq ".inf" }).Count)
    $state.has_cat = [bool](@($files | Where-Object { $_.Extension -ieq ".cat" }).Count)
    $state.has_sys = [bool](@($files | Where-Object { $_.Extension -ieq ".sys" }).Count)
    $state.has_complete_payload = [bool]($state.has_inf -and $state.has_cat -and $state.has_sys)
    $state.catalog_signature_status = Get-CatalogSignatureStatus -Root $Root
    $state.has_trusted_signature = [bool]($state.catalog_signature_status -eq "Valid")
    $state.has_real_payload = [bool]($state.has_complete_payload -and $state.has_trusted_signature)

    if ($state.has_real_payload -and $state.has_manifest) {
        $state.status = "real_payload_ready"
    } elseif ($state.has_real_payload) {
        $state.status = "real_payload_missing_manifest"
    } elseif ($state.has_complete_payload) {
        $state.status = "unsigned_or_untrusted_payload"
    } elseif ($nonPlaceholderFiles.Count -eq 0) {
        $state.status = "placeholder_only"
    } else {
        $state.status = "partial_payload"
    }

    return [pscustomobject]$state
}

function Invoke-ServicePipeCommand {
    param(
        [string]$Command,
        [hashtable]$Payload = @{},
        [int]$TimeoutMs = 4000
    )

    $pipeName = "CMDB-Agent-Privileged"
    $client = $null
    $writer = $null
    $reader = $null
    try {
        $client = New-Object System.IO.Pipes.NamedPipeClientStream(
            ".",
            $pipeName,
            [System.IO.Pipes.PipeDirection]::InOut,
            [System.IO.Pipes.PipeOptions]::None
        )
        $client.Connect($TimeoutMs)
        try {
            $client.ReadMode = [System.IO.Pipes.PipeTransmissionMode]::Message
        } catch {}

        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $writer = New-Object System.IO.StreamWriter($client, $utf8)
        $writer.AutoFlush = $true
        $reader = New-Object System.IO.StreamReader($client, $utf8)

        $request = @{
            command = $Command
            payload = $Payload
            request_id = [guid]::NewGuid().ToString()
        } | ConvertTo-Json -Depth 10 -Compress

        $writer.WriteLine($request)
        $responseLine = $reader.ReadLine()
        if (-not $responseLine) {
            throw "empty response from service pipe"
        }

        $response = $responseLine | ConvertFrom-Json
        if (-not $response.ok) {
            throw [string]$response.error
        }
        return $response.payload
    } finally {
        foreach ($resource in @($reader, $writer, $client)) {
            if (-not $resource) {
                continue
            }
            try {
                $resource.Dispose()
            } catch {
                # Service may close the named pipe immediately after replying.
                # Disposal noise here should not hide the actual readiness result.
            }
        }
    }
}

function Write-ObjectJson {
    param(
        [string]$Prefix,
        [object]$Value,
        [int]$Depth = 8
    )

    try {
        ($Value | ConvertTo-Json -Depth $Depth) -split "`r?`n" | ForEach-Object {
            Write-Diag ($Prefix + $_)
        }
    } catch {
        Write-Diag ($Prefix + "<json serialization failed: " + $_.Exception.Message + ">")
    }
}

Set-Content -LiteralPath $LogPath -Value ("==== Z-View Diagnostic " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") + " ====") -Encoding UTF8

Write-Host "============================================"
Write-Host "Z-View Diagnostic Tool"
Write-Host "============================================"
Write-Host ""
Write-Host "Detailed report: $LogPath"

Write-Section "Basic Environment"
Test-CommandResult "PowerShell availability" { $PSVersionTable.PSVersion.ToString() }
Test-CommandResult "Win32_BIOS SerialNumber" {
    $value = $null
    try { $value = Get-CimInstance Win32_BIOS | Select-Object -ExpandProperty SerialNumber } catch {}
    if (-not $value) { try { $value = Get-WmiObject Win32_BIOS | Select-Object -ExpandProperty SerialNumber } catch {} }
    if (-not $value) { throw "BIOS serial number is empty" }
    $value
}
Test-CommandResult "Win32_ComputerSystem Manufacturer" {
    $value = $null
    try { $value = Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer } catch {}
    if (-not $value) { try { $value = Get-WmiObject Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer } catch {} }
    if (-not $value) { throw "manufacturer is empty" }
    $value
}
Test-CommandResult "Win32_ComputerSystem Model" {
    $value = $null
    try { $value = Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Model } catch {}
    if (-not $value) { try { $value = Get-WmiObject Win32_ComputerSystem | Select-Object -ExpandProperty Model } catch {} }
    if (-not $value) { throw "model is empty" }
    $value
}
Test-CommandResult "Software registry access" {
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
    if (-not $key) { throw "HKLM uninstall registry key is not accessible" }
    "Subkeys count: " + $key.SubKeyCount
}

Write-Section "Service"
$svc = Get-CimInstance Win32_Service -Filter "Name='CMDB-Agent'" -ErrorAction SilentlyContinue
if ($svc) {
    Write-Diag ("name={0} state={1} start={2} account={3}" -f $svc.Name, $svc.State, $svc.StartMode, $svc.StartName)
    Write-Diag ("path=" + $svc.PathName)
} else {
    Write-Diag "CMDB-Agent service not found"
}

Write-Section "Service Remote Desktop Readiness"
try {
    $readiness = Invoke-ServicePipeCommand -Command "get_remote_desktop_readiness"
    $substrate = Invoke-ServicePipeCommand -Command "get_display_substrate"
    $virtualDisplay = Invoke-ServicePipeCommand -Command "get_virtual_display_status" -Payload @{ force_refresh = $true }

    Write-Diag ("continuity_grade=" + [string]$readiness.continuity_grade)
    Write-Diag ("commercial_continuity_ready=" + [string][bool]$readiness.commercial_continuity_ready)
    Write-Diag ("continuity_blocked_by_missing_substrate=" + [string][bool]$readiness.continuity_blocked_by_missing_substrate)
    Write-Diag ("preferred_capture_host_session_id=" + [string]$readiness.preferred_capture_host_session_id)
    Write-Diag ("active_capture_host_session_id=" + [string]$readiness.active_capture_host_session_id)
    Write-Diag ("virtual_display_provisioning_state=" + [string]$readiness.virtual_display_provisioning_state)
    Write-Diag ("virtual_display_package_root=" + [string]$virtualDisplay.package_root)
    Write-Diag ("virtual_display_package_root_exists=" + [string][bool]$virtualDisplay.package_root_exists)

    $blockers = @($readiness.continuity_blockers)
    if ($blockers.Count -gt 0) {
        Write-Diag ("continuity_blockers=" + ($blockers -join ", "))
    } else {
        Write-Diag "continuity_blockers=none"
    }

    $requirements = @($readiness.continuity_requirements)
    if ($requirements.Count -gt 0) {
        Write-Diag ("continuity_requirements=" + ($requirements -join ", "))
    } else {
        Write-Diag "continuity_requirements=none"
    }

    $substrateLine = "display_substrate_state=" `
        + [string]$substrate.provider_state `
        + " persistent_available=" + [string][bool]$substrate.persistent_available `
        + " persistent_ready_for_unattended=" + [string][bool]$substrate.persistent_ready_for_unattended `
        + " physical_display_attached=" + [string][bool]$substrate.physical_display_attached `
        + " virtual_display_attached=" + [string][bool]$substrate.virtual_display_attached
    Write-Diag $substrateLine

    $virtualProviderLine = "virtual_display_provider_state=" `
        + [string]$virtualDisplay.provisioning_state `
        + " driver_package_complete=" + [string][bool]$virtualDisplay.driver_package_complete `
        + " driver_package_signed=" + [string][bool]$virtualDisplay.driver_package_signed `
        + " catalog_signature=" + [string]$virtualDisplay.driver_catalog_signature_status `
        + " installed_device_present=" + [string][bool]$virtualDisplay.installed_device_present `
        + " attached_virtual_display=" + [string][bool]$virtualDisplay.attached_virtual_display
    Write-Diag $virtualProviderLine

    $virtualAttachmentLine = "virtual_display_attachment=" `
        + " device_attached_to_desktop=" + [string][bool]$virtualDisplay.device_attached_to_desktop `
        + " device_attached_confidence=" + [string]$virtualDisplay.device_attached_confidence `
        + " display_inventory_virtual_adapter_count=" + [string]$virtualDisplay.display_inventory_virtual_adapter_count `
        + " display_inventory_virtual_attached_count=" + [string]$virtualDisplay.display_inventory_virtual_attached_count `
        + " display_inventory_attached_display_count=" + [string]$virtualDisplay.display_inventory_attached_display_count `
        + " display_inventory_render_monitor_count=" + [string]$virtualDisplay.display_inventory_render_monitor_count `
        + " display_inventory_remote_adapter_present=" + [string][bool]$virtualDisplay.display_inventory_remote_adapter_present
    Write-Diag $virtualAttachmentLine

    Write-ObjectJson -Prefix "readiness: " -Value $readiness
    Write-ObjectJson -Prefix "substrate: " -Value $substrate
    Write-ObjectJson -Prefix "virtual_display: " -Value $virtualDisplay
} catch {
    Write-Diag ("service readiness query failed: " + $_.Exception.Message)
}

Write-Section "Virtual Display Payload Locations"
foreach ($root in @(
    (Join-Path $PackageDir "Drivers\VirtualDisplay"),
    (Join-Path $InstallDir "Drivers\VirtualDisplay"),
    (Join-Path $DataDir "Drivers\VirtualDisplay")
)) {
    $payload = Get-VirtualDisplayPayloadState -Root $root
    Write-Diag (
        "payload_root={0} status={1} exists={2} files={3} manifest={4} inf={5} cat={6} sys={7} catalog_signature={8}" -f
        $payload.root,
        $payload.status,
        $payload.exists,
        $payload.file_count,
        $payload.has_manifest,
        $payload.has_inf,
        $payload.has_cat,
        $payload.has_sys,
        $payload.catalog_signature_status
    )
}

Write-Section "Executable Fingerprints"
foreach ($path in @(
    (Join-Path $InstallDir "Z-View.exe"),
    (Join-Path $PackageDir "Z-View.exe"),
    (Join-Path $InstallDir "CMDB-Agent.exe")
)) {
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        $hash = Get-Sha256 -Path $path
        Write-Diag ("{0} size={1} mtime={2} sha256={3}" -f $path, $item.Length, $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $hash)
    } else {
        Write-Diag "$path missing"
    }
}

Write-Section "Processes"
$agentProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("Z-View.exe", "CMDB-Agent.exe", "python.exe") -and ($_.CommandLine -match "Z-View|CMDB-Agent|cmdb_agent") } |
    Sort-Object SessionId, ProcessId)
if ($agentProcesses.Count -eq 0) {
    Write-Diag "No Z-View/CMDB-Agent process found"
}
foreach ($proc in $agentProcesses) {
    Write-Diag ("pid={0} session={1} name={2} cmd={3}" -f $proc.ProcessId, $proc.SessionId, $proc.Name, (($proc.CommandLine -replace "\s+", " ").Trim()))
}

Write-Section "Port 9000 Owner"
$listeners = @(Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -eq 0) {
    Write-Diag "No TCP listener on port 9000"
    Write-Diag "[FAIL] port 9000 has no listener"
}
$activeConsoleSession = Get-ActiveConsoleSessionId
$explorerSessions = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty SessionId -Unique |
    Where-Object { $_ -gt 0 })
$interactiveSessions = @($explorerSessions)
if ($activeConsoleSession -is [int] -or $activeConsoleSession -is [uint32] -or "$activeConsoleSession" -match "^\d+$") {
    $activeConsoleNumber = [int]$activeConsoleSession
    if ($activeConsoleNumber -gt 0 -and $activeConsoleNumber -ne 65535) {
        $interactiveSessions += $activeConsoleNumber
    }
}
$interactiveSessions = @($interactiveSessions | Sort-Object -Unique)
foreach ($listener in $listeners) {
    $owner = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess) -ErrorAction SilentlyContinue
    if ($owner) {
        $ownerCommand = (($owner.CommandLine -replace "\s+", " ").Trim())
        Write-Diag ("port=9000 pid={0} session={1} cmd={2}" -f $owner.ProcessId, $owner.SessionId, $ownerCommand)
        $isUserSessionAgent = $owner.Name -eq "Z-View.exe" -and
            $ownerCommand -like "*--user-session-agent*" -and
            $ownerCommand -notlike "*--service-host*" -and
            $ownerCommand -notlike "*--run-agent*" -and
            $ownerCommand -notlike "*--consent-ui*"
        if ($isUserSessionAgent) {
            Write-Diag "[OK] port 9000 owner is user-session-agent"
        } else {
            Write-Diag "[FAIL] port 9000 owner is not user-session-agent"
        }
        if ($interactiveSessions.Count -eq 0) {
            Write-Diag "[FAIL] no interactive desktop session detected for port 9000 comparison"
        } elseif ($interactiveSessions -contains [int]$owner.SessionId) {
            Write-Diag "[OK] port 9000 owner session matches interactive desktop"
        } else {
            Write-Diag ("[FAIL] port 9000 owner session does not match interactive desktop; expected_session={0}" -f (($interactiveSessions | Sort-Object -Unique) -join ","))
        }
    } else {
        Write-Diag ("port=9000 pid={0} process_not_found" -f $listener.OwningProcess)
        Write-Diag "[FAIL] port 9000 owner process was not found"
    }
}

Write-Section "Agent Control Port"
$ControlPort = 9001
$InstalledConfigPath = Join-Path $InstallDir "config.json"
if (Test-Path -LiteralPath $InstalledConfigPath) {
    try {
        $InstalledConfig = Get-Content -LiteralPath $InstalledConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($InstalledConfig.control_port) {
            $ControlPort = [int]$InstalledConfig.control_port
        }
    } catch {
        Write-Diag ("Unable to parse control_port from installed config: " + $_.Exception.Message)
    }
}

$controlListeners = @(Get-NetTCPConnection -LocalPort $ControlPort -State Listen -ErrorAction SilentlyContinue)
if ($controlListeners.Count -eq 0) {
    Write-Diag ("[FAIL] Agent control port {0} has no listener" -f $ControlPort)
}
foreach ($listener in $controlListeners) {
    $owner = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $listener.OwningProcess) -ErrorAction SilentlyContinue
    if ($owner) {
        $ownerCommand = (($owner.CommandLine -replace "\s+", " ").Trim())
        Write-Diag ("port={0} pid={1} session={2} cmd={3}" -f $ControlPort, $owner.ProcessId, $owner.SessionId, $ownerCommand)
        $isBackendAgent = $owner.Name -eq "Z-View.exe" -and
            $ownerCommand -like "*--run-agent*" -and
            $ownerCommand -notlike "*--user-session-agent*"
        if ($isBackendAgent) {
            Write-Diag "[OK] Agent control port owner is backend agent"
        } else {
            Write-Diag "[FAIL] Agent control port owner is not backend agent"
        }
    } else {
        Write-Diag ("[FAIL] control port {0} owner process was not found pid={1}" -f $ControlPort, $listener.OwningProcess)
    }
}

Write-Section "Interactive Desktop"
Write-Diag ("active_console_session=" + (Get-ActiveConsoleSessionId))
$explorers = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction SilentlyContinue | Sort-Object SessionId, ProcessId)
if ($explorers.Count -eq 0) {
    Write-Diag "No explorer.exe found; user desktop may be locked or not logged in"
}
foreach ($explorer in $explorers) {
    try { $owner = Invoke-CimMethod -InputObject $explorer -MethodName GetOwner -ErrorAction Stop } catch { $owner = $null }
    $user = "unknown"
    if ($owner -and $owner.User) {
        $user = if ($owner.Domain) { $owner.Domain + "\" + $owner.User } else { $owner.User }
    }
    Write-Diag ("explorer pid={0} session={1} user={2}" -f $explorer.ProcessId, $explorer.SessionId, $user)
}

Write-Section "Runtime Heartbeats"
if (Test-Path -LiteralPath $RuntimeDir) {
    $heartbeatFiles = @(Get-ChildItem -LiteralPath $RuntimeDir -Filter "*.json" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($heartbeatFiles.Count -eq 0) {
        Write-Diag "No runtime heartbeat json files found"
    }
    foreach ($file in $heartbeatFiles) {
        try {
            $payload = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Diag ("{0} pid={1} session={2} updated={3}" -f $file.Name, $payload.pid, $payload.session_id, $payload.updated_at_iso)
        } catch {
            Write-Diag ($file.Name + " unreadable")
        }
    }
} else {
    Write-Diag "$RuntimeDir missing"
}

Write-Section "Recent Runtime Log Signals"
if (Test-Path -LiteralPath $RuntimeLog) {
    Get-Content -LiteralPath $RuntimeLog -Tail 180 -Encoding UTF8 |
        Where-Object { $_ -match "runtime fingerprint|WTSQueryUserToken|user-session|SendInput failed|SetCursorPos failed|Screen capture failed|capture_empty|WinError 5|拒绝访问|access denied|active_console|session=" } |
        Select-Object -Last 90 |
        ForEach-Object { Write-Diag $_ }
} else {
    Write-Diag "$RuntimeLog missing"
}

Write-Section "Recent Deploy Log"
if (Test-Path -LiteralPath $DeployLog) {
    Get-Content -LiteralPath $DeployLog -Tail 80 -Encoding UTF8 | ForEach-Object { Write-Diag $_ }
} else {
    Write-Diag "$DeployLog missing"
}

Write-Section "OS Version"
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    Write-Diag ("{0} version={1} build={2}" -f $os.Caption, $os.Version, $os.BuildNumber)
} catch {
    Write-Diag ("OS query failed: " + $_.Exception.Message)
}

Write-Diag ""
Write-Diag "Expected remote desktop state:"
Write-Diag "- CMDB-Agent service account should be LocalSystem."
Write-Diag "- Port 9000 should be owned by Z-View.exe --user-session-agent."
Write-Diag "- The --user-session-agent session should match active_console_session or explorer.exe session."
Write-Diag "- SendInput last_error=5 plus GDI WinError 5 means the process is not inside the operable interactive desktop."
Write-Diag "- commercial_continuity_ready requires a persistent display substrate: physical display or a real Windows-supported virtual display payload."
Write-Diag "- payload_root status=placeholder_only means deploy succeeded but no production virtual display driver was shipped."
Write-Diag "- payload_root status=unsigned_or_untrusted_payload means driver files exist but catalog signature is not trusted."
Write-Diag ""
Write-Diag "==== End Diagnostic ===="
