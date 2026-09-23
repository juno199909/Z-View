param(
    [int]$Samples = 8,
    [int]$IntervalSeconds = 1,
    [int]$TimeoutMs = 15000,
    [int]$Quality = 55,
    [double]$Scale = 0.5
)

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
        } | ConvertTo-Json -Depth 20 -Compress

        $writer.WriteLine($request)
        $responseLine = $reader.ReadLine()
        if (-not $responseLine) {
            throw "empty response from service pipe"
        }

        $response = $responseLine | ConvertFrom-Json -Depth 30
        if (-not $response.ok) {
            throw [string]$response.error
        }
        return $response.payload
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($writer) { $writer.Dispose() }
        if ($client) { $client.Dispose() }
    }
}

function Get-StringValue {
    param($Value)
    if ($null -eq $Value) { return "" }
    return [string]$Value
}

if ($Samples -lt 2) { $Samples = 2 }
if ($Samples -gt 60) { $Samples = 60 }
if ($IntervalSeconds -lt 1) { $IntervalSeconds = 1 }
if ($IntervalSeconds -gt 10) { $IntervalSeconds = 10 }
if ($Quality -lt 25) { $Quality = 25 }
if ($Quality -gt 95) { $Quality = 95 }
if ($Scale -lt 0.2) { $Scale = 0.2 }
if ($Scale -gt 1.0) { $Scale = 1.0 }

try {
    $readiness = Invoke-ServicePipeCommand -Command "get_remote_desktop_readiness" -TimeoutMs $TimeoutMs
    $substrate = Invoke-ServicePipeCommand -Command "get_display_substrate" -TimeoutMs $TimeoutMs
    $virtualDisplay = Invoke-ServicePipeCommand -Command "get_virtual_display_status" -Payload @{ force_refresh = $true } -TimeoutMs $TimeoutMs
} catch {
    Write-Output ("frame_liveness=query_failed error=" + $_.Exception.Message)
    exit 1
}

$ready = [bool]$readiness.commercial_continuity_ready
$persistent = [bool]$substrate.persistent_ready_for_unattended
$blocked = [bool]$readiness.continuity_blocked_by_missing_substrate -or -not $persistent
$blockers = @($readiness.continuity_blockers)
if ($blockers.Count -eq 0) {
    $blockerText = "none"
} else {
    $blockerText = ($blockers -join ",")
}

Write-Output ("frame_liveness=readiness commercial_ready=" + [string]$ready `
    + " persistent_ready_for_unattended=" + [string]$persistent `
    + " blockers=" + $blockerText)
Write-Output ("frame_liveness=display_substrate physical_display_attached=" + [string][bool]$substrate.physical_display_attached `
    + " virtual_display_attached=" + [string][bool]$substrate.virtual_display_attached `
    + " provider_state=" + (Get-StringValue $substrate.provider_state))
Write-Output ("frame_liveness=virtual_display provisioning_state=" + (Get-StringValue $virtualDisplay.provisioning_state) `
    + " package_complete=" + [string][bool]$virtualDisplay.driver_package_complete `
    + " package_signed=" + [string][bool]$virtualDisplay.driver_package_signed `
    + " catalog_signature=" + (Get-StringValue $virtualDisplay.driver_catalog_signature_status))

if ($blocked) {
    Write-Output "frame_liveness=result value=blocked"
    exit 2
}

$previousSignature = $null
$samplesOut = @()

for ($i = 1; $i -le $Samples; $i++) {
    try {
        $capturePayload = @{
            action = "capture_frame"
            payload = @{
                quality = $Quality
                scale = $Scale
                reason = "frame_liveness_probe"
                include_desktop_state = $true
            }
        }
        if ($previousSignature) {
            $capturePayload.payload["previous_signature"] = $previousSignature
        }

        $result = Invoke-ServicePipeCommand -Command "invoke_admin_action" -Payload $capturePayload -TimeoutMs $TimeoutMs
        $helper = $result.helper_response
        if ($null -eq $helper) {
            $helper = $result
        }

        $presence = $helper.display_presence
        if ($null -eq $presence) {
            $presence = @{}
        }

        $signature = Get-StringValue $helper.signature
        $sample = [pscustomobject]@{
            index = $i
            captured = [bool]$helper.captured
            unchanged = [bool]$helper.unchanged
            signature = $signature
            captured_at = Get-StringValue $helper.captured_at
            backend = Get-StringValue $helper.backend
            session_id = Get-StringValue $helper.session_id
            blocker = Get-StringValue $helper.blocker
            substrate_class = Get-StringValue $presence.substrate_class
            desktop_signature = Get-StringValue $helper.desktop_signature
            error = ""
        }
        if ($signature) {
            $previousSignature = $signature
        }
    } catch {
        $sample = [pscustomobject]@{
            index = $i
            captured = $false
            unchanged = $false
            signature = ""
            captured_at = ""
            backend = ""
            session_id = ""
            blocker = ""
            substrate_class = ""
            desktop_signature = ""
            error = $_.Exception.Message
        }
    }

    $samplesOut += $sample
    Write-Output ("frame_liveness=sample index=" + $sample.index `
        + " captured=" + [string]$sample.captured `
        + " unchanged=" + [string]$sample.unchanged `
        + " signature=" + $sample.signature `
        + " captured_at=" + $sample.captured_at `
        + " backend=" + $sample.backend `
        + " session_id=" + $sample.session_id `
        + " blocker=" + $sample.blocker `
        + " substrate_class=" + $sample.substrate_class `
        + " error=" + $sample.error)

    if ($i -lt $Samples) {
        Start-Sleep -Seconds $IntervalSeconds
    }
}

$capturedSamples = @($samplesOut | Where-Object { $_.captured -and $_.signature })
$uniqueSignatures = @($capturedSamples | Select-Object -ExpandProperty signature -Unique)
$failedSamples = @($samplesOut | Where-Object { -not $_.captured })

Write-Output ("frame_liveness=summary samples=" + $Samples `
    + " captured=" + $capturedSamples.Count `
    + " failed=" + $failedSamples.Count `
    + " unique_signatures=" + $uniqueSignatures.Count)

if ($capturedSamples.Count -lt 2) {
    Write-Output "frame_liveness=result value=stale_or_not_live"
    exit 3
}

if ($uniqueSignatures.Count -ge 2) {
    Write-Output "frame_liveness=result value=live"
    exit 0
}

Write-Output "frame_liveness=result value=stale_or_not_live"
exit 3
