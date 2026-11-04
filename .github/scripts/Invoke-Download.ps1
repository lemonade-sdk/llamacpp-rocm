# Download a file, retrying on any failure.
#
# Invoke-WebRequest's own -MaximumRetryCount only retries when the server
# answers with an error status (4xx, 5xx, 304). A server that never answers --
# a connection timeout, which is what usually happens on a hosted runner --
# fails on the first attempt. This retries those too.
#
# Usage, after actions/checkout:
#   . .github/scripts/Invoke-Download.ps1
#   Invoke-Download -Uri $url -OutFile $path

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$Attempts = 5
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile
            return
        } catch {
            if ($attempt -ge $Attempts) {
                Write-Host "Download of $Uri failed after $Attempts attempts."
                throw
            }
            $delay = [Math]::Min(60, 15 * $attempt)
            Write-Host "Download of $Uri failed (attempt $attempt/$Attempts): $($_.Exception.Message). Retrying in ${delay}s..."
            Remove-Item -Force $OutFile -ErrorAction SilentlyContinue
            Start-Sleep -Seconds $delay
        }
    }
}
