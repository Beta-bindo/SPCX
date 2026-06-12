param(
    [Parameter(Mandatory = $true)]
    [string]$LogPath
)

$cmdLine = $env:TEE_CMD
if ([string]::IsNullOrWhiteSpace($cmdLine)) {
    Write-Error 'TEE_CMD environment variable is not set'
    exit 1
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'cmd.exe'
$psi.Arguments = "/c $cmdLine 2>&1"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.CreateNoWindow = $true

$p = New-Object System.Diagnostics.Process
$p.StartInfo = $psi
[void]$p.Start()

while (($line = $p.StandardOutput.ReadLine()) -ne $null) {
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding Default
}

$p.WaitForExit()
exit $p.ExitCode
