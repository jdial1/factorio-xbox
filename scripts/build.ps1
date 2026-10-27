# Builds and signs the launcher package (build\FactorioXbox.msix, with its VCLibs dependency and certificate).
#
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-HostRepo ..\xbox-pc-host] [-Configuration Release]
#
# Needs Visual Studio 2022 (or its Build Tools) with "Universal Windows Platform development" and the
# "C++ (v143) Universal Windows Platform tools", a Windows 11 SDK, and an xbox-pc-host checkout (default:
# next to this repo). Signing uses a certificate in CurrentUser\My whose subject is the manifest's Publisher
# (CN=FactorioXbox); the first build makes a self-signed one and exports its .cer next to the package.
param(
    [string]$HostRepo = (Join-Path $PSScriptRoot '..\..\xbox-pc-host'),
    [ValidateSet('Release', 'Debug')][string]$Configuration = 'Release',
    [string]$Out = (Join-Path $PSScriptRoot '..\build')
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$HostRepo = (Resolve-Path $HostRepo -ErrorAction SilentlyContinue).Path
if (-not $HostRepo -or -not (Test-Path (Join-Path $HostRepo 'XboxPcHost\src\App.cpp'))) {
    throw "xbox-pc-host not found; clone it next to this repo or pass -HostRepo <path>"
}
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path

# Visual Studio's MSBuild.
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild not found (install Visual Studio 2022 or its Build Tools)' }

# The manifest's publisher and version.
$manifest = [xml](Get-Content (Join-Path $repo 'app\Package.appxmanifest'))
$publisher = $manifest.Package.Identity.Publisher
$version = $manifest.Package.Identity.Version
Write-Host "FactorioXbox $version ($publisher), host sources from $HostRepo"

$common = @("$repo\FactorioXbox.sln", "/p:Configuration=$Configuration", '/p:Platform=x64', "/p:HostRepo=$HostRepo\", '/m', '/nologo', '/v:minimal')
& $msbuild @common '/t:Restore' '/p:RestorePackagesConfig=true'
if ($LASTEXITCODE) { throw 'NuGet restore failed' }
$pkgDir = Join-Path $repo 'app\AppPackages\'
& $msbuild @common '/p:GenerateAppxPackageOnBuild=true' '/p:UapAppxPackageBuildMode=SideloadOnly' '/p:AppxBundle=Never' `
    '/p:AppxPackageSigningEnabled=false' "/p:AppxPackageDir=$pkgDir"
if ($LASTEXITCODE) { throw 'build failed' }

$msix = Get-ChildItem $pkgDir -Recurse -Filter "FactorioXbox_$($version)_x64*.msix" | Sort-Object LastWriteTime | Select-Object -Last 1
$vclibs = Get-ChildItem $pkgDir -Recurse -Filter 'Microsoft.VCLibs.x64.14.00.appx' | Where-Object { $_.FullName -notmatch 'Desktop' } | Select-Object -Last 1
if (-not $msix) { throw "no package for version $version under $pkgDir" }

# Signing certificate: the publisher's, made once if there is none.
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $publisher -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } | Select-Object -First 1
if (-not $cert) {
    Write-Host "making a self-signed code-signing certificate $publisher"
    $cert = New-SelfSignedCertificate -Type Custom -Subject $publisher -KeyUsage DigitalSignature -FriendlyName 'FactorioXbox test signing' `
        -CertStoreLocation Cert:\CurrentUser\My -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
}
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Sort-Object FullName | Select-Object -Last 1
if (-not $signtool) { throw 'signtool not found (install a Windows 10/11 SDK)' }

Copy-Item $msix.FullName (Join-Path $Out 'FactorioXbox.msix') -Force
if ($vclibs) { Copy-Item $vclibs.FullName (Join-Path $Out 'Microsoft.VCLibs.x64.14.00.appx') -Force }
Export-Certificate -Cert $cert -FilePath (Join-Path $Out 'FactorioXbox.cer') | Out-Null
& $signtool.FullName sign /fd SHA256 /sha1 $cert.Thumbprint (Join-Path $Out 'FactorioXbox.msix') | Out-Null
if ($LASTEXITCODE) { throw 'signing failed' }
Write-Host "built $Out\FactorioXbox.msix ($([math]::Round((Get-Item (Join-Path $Out 'FactorioXbox.msix')).Length / 1MB, 1)) MB), signed by $publisher"
Write-Host 'next: scripts\prepare.py <your Factorio folder>, then scripts\deploy.py all --console <Xbox IP>'
