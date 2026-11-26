param(
    [string]$Ref = "v0.1.0",
    [switch]$DryRun
)

$commands = @(
    "uv tool install --python 3.12 --torch-backend=auto `"git+https://github.com/abm-dungtq/tqd-voice@$Ref`"",
    "& (Join-Path (uv tool dir --bin) 'tqd-voice.exe') doctor",
    "& (Join-Path (uv tool dir --bin) 'tqd-voice.exe') install"
)

if ($DryRun) {
    $commands | ForEach-Object { Write-Output $_ }
    return
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output 'Chưa có uv. Cài bằng: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return
}

uv tool install --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voice@$Ref"
if ($LASTEXITCODE -ne 0) { return }

uv tool update-shell | Out-Null

$exe = Join-Path (uv tool dir --bin) "tqd-voice.exe"

& $exe doctor
if ($LASTEXITCODE -ne 0) { Write-Warning "doctor báo lỗi; 'tqd-voice install' sẽ tự dừng nếu lỗi chặn cài đặt." }

& $exe install
return
