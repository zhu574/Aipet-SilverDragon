[CmdletBinding()]
param(
    [string]$CodexDirectory,
    [string]$PackageDirectory
)

$ErrorActionPreference = 'Stop'
$ProjectDirectory = Split-Path -Parent $PSScriptRoot
if (-not $PackageDirectory) {
    $PackageDirectory = Join-Path $ProjectDirectory 'releases\v0.1.0-preview'
}
$PackageDirectory = (Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not $CodexDirectory) {
    if ($env:CODEX_HOME) {
        $CodexDirectory = $env:CODEX_HOME
    } else {
        $CodexDirectory = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
    }
}
$CodexDirectory = [IO.Path]::GetFullPath($CodexDirectory)
$MetadataPath = Join-Path $PackageDirectory 'pet.json'
$Metadata = Get-Content -LiteralPath $MetadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Metadata.id -ne 'silver-dragon-preview' -or
    $Metadata.spriteVersionNumber -ne 1 -or
    $Metadata.spritesheetPath -ne 'spritesheet.webp') {
    throw 'Unexpected preview package contract.'
}
$Checksums = Get-Content -LiteralPath (Join-Path $PackageDirectory 'checksums.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$PackageFiles = @('pet.json', 'spritesheet.webp')
foreach ($Name in $PackageFiles) {
    $ActualHash = (Get-FileHash -LiteralPath (Join-Path $PackageDirectory $Name) -Algorithm SHA256).Hash
    if ($ActualHash -ne $Checksums.$Name) {
        throw "Package checksum mismatch: $Name"
    }
}
$Destination = Join-Path (Join-Path $CodexDirectory 'pets') 'silver-dragon-preview'
$NeedsBackup = $false
foreach ($Name in $PackageFiles) {
    $Existing = Join-Path $Destination $Name
    if (Test-Path -LiteralPath $Existing -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $Existing -Algorithm SHA256).Hash -ne $Checksums.$Name) {
            $NeedsBackup = $true
        }
    }
}
if ($NeedsBackup) {
    $BackupDirectory = Join-Path $ProjectDirectory ('.local\install-backups\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
    foreach ($Name in $PackageFiles) {
        $Existing = Join-Path $Destination $Name
        if (Test-Path -LiteralPath $Existing -PathType Leaf) {
            Copy-Item -LiteralPath $Existing -Destination (Join-Path $BackupDirectory $Name)
        }
    }
}
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
foreach ($Name in $PackageFiles) {
    Copy-Item -LiteralPath (Join-Path $PackageDirectory $Name) -Destination (Join-Path $Destination $Name) -Force
    if ((Get-FileHash -LiteralPath (Join-Path $Destination $Name) -Algorithm SHA256).Hash -ne $Checksums.$Name) {
        throw "Installed checksum mismatch: $Name"
    }
}
$LocalDirectory = Join-Path $ProjectDirectory '.local'
New-Item -ItemType Directory -Path $LocalDirectory -Force | Out-Null
$Result = [ordered]@{
    installed = $true
    petId = $Metadata.id
    spriteVersionNumber = 1
    package = $PackageDirectory
    destination = $Destination
    installedAtUtc = [DateTime]::UtcNow.ToString('o')
    appLoadVerified = $false
}
$Result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $LocalDirectory 'installation.json') -Encoding UTF8
$Result | ConvertTo-Json
