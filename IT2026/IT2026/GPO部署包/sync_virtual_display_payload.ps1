param(
    [string]$SourceRoot = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "Drivers\VirtualDisplay"),
    [string]$InstallRoot = "C:\Program Files\CMDB-Agent",
    [string]$DataRoot = "C:\ProgramData\CMDB-Agent"
)

$ErrorActionPreference = "Stop"

function Write-Status {
    param([string]$Message)
    Write-Output $Message
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

function Get-PayloadState {
    param([string]$Root)

    $exists = Test-Path -LiteralPath $Root -PathType Container
    $resolved = $Root
    try {
        if ($exists) {
            $resolved = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
        }
    } catch {}

    $state = [ordered]@{
        root = $resolved
        exists = $exists
        file_count = 0
        has_manifest = $false
        has_manifest_example = $false
        has_inf = $false
        has_cat = $false
        has_sys = $false
        has_complete_payload = $false
        catalog_signature_status = "missing"
        has_trusted_signature = $false
        non_placeholder_file_count = 0
        has_real_payload = $false
        status = "missing"
    }

    if (-not $exists) {
        return [pscustomobject]$state
    }

    $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue)
    $placeholderNames = @("README.md", "driver_manifest.json.example")
    $nonPlaceholderFiles = @($files | Where-Object { $placeholderNames -notcontains $_.Name })

    $state.file_count = $files.Count
    $state.has_manifest = Test-Path -LiteralPath (Join-Path $Root "driver_manifest.json") -PathType Leaf
    $state.has_manifest_example = Test-Path -LiteralPath (Join-Path $Root "driver_manifest.json.example") -PathType Leaf
    $state.has_inf = [bool](@($files | Where-Object { $_.Extension -ieq ".inf" }).Count)
    $state.has_cat = [bool](@($files | Where-Object { $_.Extension -ieq ".cat" }).Count)
    $state.has_sys = [bool](@($files | Where-Object { $_.Extension -ieq ".sys" }).Count)
    $state.has_complete_payload = [bool]($state.has_inf -and $state.has_cat -and $state.has_sys)
    $state.catalog_signature_status = Get-CatalogSignatureStatus -Root $Root
    $state.has_trusted_signature = [bool]($state.catalog_signature_status -eq "Valid")
    $state.non_placeholder_file_count = $nonPlaceholderFiles.Count
    $state.has_real_payload = [bool]($state.has_complete_payload -and $state.has_trusted_signature)

    if ($state.has_real_payload -and $state.has_manifest) {
        $state.status = "real_payload_ready"
    } elseif ($state.has_real_payload) {
        $state.status = "real_payload_missing_manifest"
    } elseif ($state.has_complete_payload) {
        $state.status = "unsigned_or_untrusted_payload"
    } elseif ($state.non_placeholder_file_count -eq 0) {
        $state.status = "placeholder_only"
    } else {
        $state.status = "partial_payload"
    }

    return [pscustomobject]$state
}

function Ensure-Directory {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Sync-Payload {
    param(
        [pscustomobject]$SourceState,
        [string]$TargetRoot
    )

    $targetStateBefore = Get-PayloadState -Root $TargetRoot
    $action = "noop"
    $note = ""

    if (-not $SourceState.exists) {
        $action = "skipped_source_missing"
        return [pscustomobject]@{
            target_root = $TargetRoot
            action = $action
            note = $note
            before = $targetStateBefore
            after = $targetStateBefore
        }
    }

    if (-not $SourceState.has_real_payload -and $targetStateBefore.has_real_payload) {
        $action = "preserved_existing_real_payload"
        $note = "source package is not a complete driver payload"
        return [pscustomobject]@{
            target_root = $TargetRoot
            action = $action
            note = $note
            before = $targetStateBefore
            after = $targetStateBefore
        }
    }

    Ensure-Directory -Path $TargetRoot
    Copy-Item -Path (Join-Path $SourceState.root "*") -Destination $TargetRoot -Recurse -Force

    $targetStateAfter = Get-PayloadState -Root $TargetRoot
    if ($SourceState.has_real_payload) {
        $action = "copied_real_payload"
    } elseif ($SourceState.status -eq "partial_payload") {
        $action = "copied_partial_payload"
        $note = "source package is incomplete; copied for diagnostics only"
    } else {
        $action = "copied_placeholder_metadata"
        $note = "source package does not contain a complete driver payload"
    }

    return [pscustomobject]@{
        target_root = $TargetRoot
        action = $action
        note = $note
        before = $targetStateBefore
        after = $targetStateAfter
    }
}

$sourceState = Get-PayloadState -Root $SourceRoot
$targetRoots = @(
    (Join-Path $InstallRoot "Drivers\VirtualDisplay"),
    (Join-Path $DataRoot "Drivers\VirtualDisplay")
)

Write-Status ("virtual_display_payload_source root={0} status={1} exists={2} files={3} manifest={4} inf={5} cat={6} sys={7} catalog_signature={8}" -f
    $sourceState.root,
    $sourceState.status,
    $sourceState.exists,
    $sourceState.file_count,
    $sourceState.has_manifest,
    $sourceState.has_inf,
    $sourceState.has_cat,
    $sourceState.has_sys,
    $sourceState.catalog_signature_status
)
if (-not $sourceState.has_real_payload) {
    Write-Status ("virtual_display_payload_warning status={0} catalog_signature={1} message=real_signed_virtual_display_driver_payload_required_for_rdp_disconnect_continuity" -f
        $sourceState.status,
        $sourceState.catalog_signature_status
    )
}

foreach ($targetRoot in $targetRoots) {
    $result = Sync-Payload -SourceState $sourceState -TargetRoot $targetRoot
    Write-Status ("virtual_display_payload_target root={0} action={1} before={2} after={3}" -f
        $result.target_root,
        $result.action,
        $result.before.status,
        $result.after.status
    )
    if ($result.note) {
        Write-Status ("virtual_display_payload_note root={0} note={1}" -f
            $result.target_root,
            $result.note
        )
    }
}
