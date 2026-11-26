param(
    [string]$Ref = "v0.1.0",
    [switch]$DryRun
)

$commands = @(
    "uv tool install --python 3.12 --torch-backend=auto `"git+https://github.com/abm-dungtq/tqd-voice@$Ref`"",
    "tqd-voice doctor",
    "tqd-voice install"
)

if ($DryRun) {
    $commands | ForEach-Object { Write-Output $_ }
    exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output 'Chưa có uv. Cài bằng: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    exit 1
}

uv tool install --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voice@$Ref"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

tqd-voice doctor
if ($LASTEXITCODE -ne 0) { Write-Warning "doctor báo lỗi; 'tqd-voice install' sẽ tự dừng nếu lỗi chặn cài đặt." }

tqd-voice install
exit $LASTEXITCODE
