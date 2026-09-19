param(
    [string]$SdkRoot = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$BuildToolsVersion = "35.0.0",
    [string]$JavaHome = "C:\Program Files\Eclipse Adoptium\jdk-17.0.8.7-hotspot",
    [string]$Serial = "emulator-5554",
    [switch]$BuildOnly
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).ProviderPath
$Output = Join-Path $Root ("build\ui-smoke-" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss-fff"))
$BuildTools = Join-Path $SdkRoot "build-tools\$BuildToolsVersion"
$AndroidJar = Join-Path $SdkRoot "platforms\android-35\android.jar"
$Adb = Join-Path $SdkRoot "platform-tools\adb.exe"
function Run-Native([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Command (exit $LASTEXITCODE)" }
}
foreach ($Directory in @("compiled", "gen", "classes", "dex")) {
    New-Item -ItemType Directory -Force (Join-Path $Output $Directory) | Out-Null
}
Run-Native "$BuildTools\aapt2.exe" @("compile", "--dir", "$Root\app\src\main\res", "-o", "$Output\compiled")
$Flats = @(Get-ChildItem "$Output\compiled" -Filter *.flat | ForEach-Object { $_.FullName })
Run-Native "$BuildTools\aapt2.exe" (@("link", "-o", "$Output\unsigned.apk", "-I", $AndroidJar,
    "--manifest", "$Root\tests\ui\AndroidManifest.xml", "--rename-manifest-package", "ru.rentalmanager.mobile.uitest",
    "--java", "$Output\gen", "--auto-add-overlay") + $Flats)
$Sources = @(Get-ChildItem "$Root\app\src\main\java", "$Root\tests\ui" -Recurse -Filter *.java | ForEach-Object { $_.FullName })
Run-Native "$JavaHome\bin\javac.exe" (@("-encoding", "UTF-8", "-source", "8", "-target", "8", "-cp", "$AndroidJar;$Output\gen", "-d", "$Output\classes") + $Sources)
Run-Native "$JavaHome\bin\jar.exe" @("cf", "$Output\classes.jar", "-C", "$Output\classes", ".")
Run-Native "$BuildTools\d8.bat" @("--release", "--min-api", "23", "--lib", $AndroidJar, "--output", "$Output\dex", "$Output\classes.jar")
Run-Native "$JavaHome\bin\jar.exe" @("uf", "$Output\unsigned.apk", "-C", "$Output\dex", "classes.dex")
Run-Native "$BuildTools\zipalign.exe" @("-p", "-f", "4", "$Output\unsigned.apk", "$Output\aligned.apk")
$TestKey = Join-Path $Root "build\ui-smoke-signing.keystore"
if (!(Test-Path -LiteralPath $TestKey)) {
    Run-Native "$JavaHome\bin\keytool.exe" @("-genkeypair", "-keystore", $TestKey, "-storepass", "android", "-keypass", "android",
        "-alias", "ui-smoke", "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650", "-dname", "CN=Rental UI Smoke")
}
$Apk = Join-Path $Output "rental-manager-ui-smoke.apk"
Run-Native "$BuildTools\apksigner.bat" @("sign", "--ks", $TestKey, "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", $Apk, "$Output\aligned.apk")
Run-Native "$BuildTools\apksigner.bat" @("verify", $Apk)
if ($BuildOnly) { Write-Output $Apk; return }
if ($Serial -notmatch '^emulator-[0-9]+$') { throw "UI smoke installation is restricted to an explicitly selected Android emulator" }
$IsEmulator = & $Adb -s $Serial shell getprop ro.kernel.qemu
if ($LASTEXITCODE -ne 0 -or ($IsEmulator -join "").Trim() -ne "1") { throw "The selected emulator is not ready" }
Run-Native $Adb @("-s", $Serial, "install", "-r", $Apk)
$RunOutput = & $Adb -s $Serial shell am instrument -w -r ru.rentalmanager.mobile.uitest/ru.rentalmanager.mobile.UiSmokeInstrumentation
$RunExit = $LASTEXITCODE
$RunOutput | Tee-Object -FilePath (Join-Path $Output "instrumentation.txt") | Write-Output
Run-Native $Adb @("-s", $Serial, "pull", "/sdcard/Android/data/ru.rentalmanager.mobile.uitest/files/ui-smoke", "$Output\screenshots")
if ($RunExit -ne 0 -or ($RunOutput -join "`n") -notmatch "UI_SMOKE_PASS") {
    throw "Native UI smoke failed; inspect instrumentation.txt and emulator logcat"
}
Write-Output "UI smoke evidence: $Output"
