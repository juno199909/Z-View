$ErrorActionPreference = "Stop"

function Invoke-ServicePipeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [hashtable]$Payload = @{},
        [int]$TimeoutMs = 5000
    )

    $client = $null
    $writer = $null
    $reader = $null
    try {
        $client = New-Object System.IO.Pipes.NamedPipeClientStream(
            ".",
            "CMDB-Agent-Privileged",
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
                # Disposal noise here should not mask a successful query.
            }
        }
    }
}

try {
    $readiness = Invoke-ServicePipeCommand -Command "get_remote_desktop_readiness"
    $substrate = Invoke-ServicePipeCommand -Command "get_display_substrate"
    $virtualDisplay = Invoke-ServicePipeCommand -Command "get_virtual_display_status" -Payload @{ force_refresh = $true }
} catch {
    Write-Output ("continuity_check=query_failed error=" + $_.Exception.Message)
    exit 1
}

$ready = [bool]$readiness.commercial_continuity_ready
$blocked = [bool]$readiness.continuity_blocked_by_missing_substrate
$blockers = @($readiness.continuity_blockers)
if ($blockers.Count -eq 0) {
    $blockerText = "none"
} else {
    $blockerText = ($blockers -join ",")
}

$result = "not_verified"
if ($ready) {
    $result = "ready"
} elseif ($blocked -or -not [bool]$substrate.persistent_ready_for_unattended) {
    $result = "blocked"
}

$driverPayload = "unknown"
if (-not [bool]$virtualDisplay.package_root_exists) {
    $driverPayload = "missing"
} elseif ([bool]$virtualDisplay.driver_package_complete -and [bool]$virtualDisplay.driver_package_signed) {
    $driverPayload = "real_signed_payload_present"
} elseif ([bool]$virtualDisplay.driver_package_complete) {
    $driverPayload = "unsigned_or_untrusted_payload"
} elseif ([string]$virtualDisplay.provisioning_state -eq "driver_package_present_missing_inf") {
    $driverPayload = "placeholder_or_missing_inf"
} elseif ([string]$virtualDisplay.provisioning_state -eq "driver_package_incomplete") {
    $driverPayload = "partial_payload"
} else {
    $driverPayload = "not_complete"
}

Write-Output ("continuity_check=result value=" + $result)
Write-Output ("continuity_check=commercial_ready value=" + $ready)
Write-Output ("continuity_check=grade value=" + [string]$readiness.continuity_grade)
Write-Output ("continuity_check=missing_substrate_blocked value=" + $blocked)
Write-Output ("continuity_check=blockers value=" + $blockerText)
$substrateLine = "continuity_check=display_substrate persistent_ready_for_unattended=" `
    + [string][bool]$substrate.persistent_ready_for_unattended `
    + " physical_display_attached=" + [string][bool]$substrate.physical_display_attached `
    + " virtual_display_attached=" + [string][bool]$substrate.virtual_display_attached `
    + " provider_state=" + [string]$substrate.provider_state
Write-Output $substrateLine

$virtualDisplayLine = "continuity_check=virtual_display provisioning_state=" `
    + [string]$virtualDisplay.provisioning_state `
    + " package_complete=" + [string][bool]$virtualDisplay.driver_package_complete `
    + " package_signed=" + [string][bool]$virtualDisplay.driver_package_signed `
    + " catalog_signature=" + [string]$virtualDisplay.driver_catalog_signature_status `
    + " driver_payload=" + $driverPayload `
    + " attached=" + [string][bool]$virtualDisplay.attached_virtual_display `
    + " package_root=" + [string]$virtualDisplay.package_root
Write-Output $virtualDisplayLine

if ($ready) {
    exit 0
}

if ($blocked -or -not [bool]$substrate.persistent_ready_for_unattended) {
    exit 2
}

exit 3
