param(
    [string]$Ref = "v0.2.0",
    [switch]$DryRun
)

$commands = @(
    "uv tool install --python 3.12 --torch-backend=auto `"tqd-voicestudio[import] @ git+https://github.com/abm-dungtq/tqd-voicestudio@$Ref`"",
    "& (Join-Path (uv tool dir --bin) 'tqd-voicestudio.exe') doctor",
    "& (Join-Path (uv tool dir --bin) 'tqd-voicestudio.exe') install"
)

if ($DryRun) {
    $commands | ForEach-Object { Write-Output $_ }
    return
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output 'Chưa có uv. Cài bằng: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return
}

uv tool install --python 3.12 --torch-backend=auto "tqd-voicestudio[import] @ git+https://github.com/abm-dungtq/tqd-voicestudio@$Ref"
if ($LASTEXITCODE -ne 0) { return }

uv tool update-shell | Out-Null

$exe = Join-Path (uv tool dir --bin) "tqd-voicestudio.exe"

& $exe doctor
if ($LASTEXITCODE -ne 0) { Write-Warning "doctor báo lỗi; 'tqd-voicestudio install' sẽ tự dừng nếu lỗi chặn cài đặt." }

& $exe install
return
