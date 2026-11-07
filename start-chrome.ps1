# Launches a separate Chrome instance with remote debugging enabled
# and an isolated profile dir. Log into Chase in this Chrome, then run discover.py.
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) {
    $chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
}
$profile = Join-Path $PSScriptRoot "chrome-debug-profile"
& $chrome --remote-debugging-port=9222 --user-data-dir="$profile"
