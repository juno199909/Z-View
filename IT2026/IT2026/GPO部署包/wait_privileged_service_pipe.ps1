[CmdletBinding()]
param(
    [string]$PipeName = 'CMDB-Agent-Privileged',
    [int]$TimeoutSeconds = 35
)

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

while ((Get-Date) -lt $deadline) {
    $client = $null
    $writer = $null
    $reader = $null
    try {
        $client = New-Object System.IO.Pipes.NamedPipeClientStream(
            '.',
            $PipeName,
            [System.IO.Pipes.PipeDirection]::InOut,
            [System.IO.Pipes.PipeOptions]::None
        )
        $client.Connect(1200)
        try {
            $client.ReadMode = [System.IO.Pipes.PipeTransmissionMode]::Message
        } catch {
        }

        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $writer = New-Object System.IO.StreamWriter($client, $utf8)
        $writer.AutoFlush = $true
        $reader = New-Object System.IO.StreamReader($client, $utf8)

        $request = @{
            command = 'ping'
            payload = @{}
            request_id = [guid]::NewGuid().ToString()
        } | ConvertTo-Json -Depth 6 -Compress

        $writer.WriteLine($request)
        $responseLine = $reader.ReadLine()
        if (-not $responseLine) {
            throw 'empty response from privileged pipe'
        }

        $response = $responseLine | ConvertFrom-Json
        if ($response.ok -and $response.payload.service_runtime -eq 'ready') {
            exit 0
        }
    } catch {
    } finally {
        try {
            if ($reader) { $reader.Dispose() }
        } catch {
        }
        try {
            if ($writer) { $writer.Dispose() }
        } catch {
        }
        try {
            if ($client) { $client.Dispose() }
        } catch {
        }
    }

    Start-Sleep -Milliseconds 500
}

exit 1
