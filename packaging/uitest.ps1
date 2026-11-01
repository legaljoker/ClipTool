# Typing test for the built ClipTool-Editor.exe (run by the Windows build).
# Starts the real program, types with the real keyboard and mouse into the
# "new project" name box, and checks the text arrived.
#   -NoFix   turn off the start-up focus fix (to show the original bug)
param([switch]$NoFix, [string]$Exe = "dist\ClipTool-Editor\ClipTool-Editor.exe", [string]$Name = "uitest")

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct POINT { public int X; public int Y; }
public static class U32 {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@

$home_ = Join-Path $PWD "uitest_home_$Name"
Remove-Item -Recurse -Force $home_ -ErrorAction SilentlyContinue
$env:CLIPTOOL_HOME = $home_
$env:CLIPTOOL_UITEST = "1"
if ($NoFix) { $env:CLIPTOOL_NO_FOCUS_FIX = "1" } else { Remove-Item Env:CLIPTOOL_NO_FOCUS_FIX -ErrorAction SilentlyContinue }
$state = Join-Path $home_ "logs\uitest.json"

function Read-State { if (Test-Path $state) { try { Get-Content $state -Raw | ConvertFrom-Json } catch { $null } } }
function Shot($file) {
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  New-Item -ItemType Directory -Force uitest_shots | Out-Null
  $bmp.Save("uitest_shots\$file", [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
}

# Control: does simulated typing work on this machine at all? (a plain Windows text box)
$form = New-Object System.Windows.Forms.Form
$tb = New-Object System.Windows.Forms.TextBox
$form.Controls.Add($tb); $form.Show(); $form.Activate(); $tb.Focus() | Out-Null
[System.Windows.Forms.SendKeys]::SendWait("ctl")
[System.Windows.Forms.Application]::DoEvents()
Write-Host "Control text box got: '$($tb.Text)'"
$form.Close()

$proc = Start-Process -FilePath $Exe -PassThru
$s = $null
for ($i = 0; $i -lt 120 -and -not ($s -and $s.rect); $i++) { Start-Sleep -Milliseconds 500; $s = Read-State }
if (-not ($s -and $s.rect)) { Write-Host "The editor page never loaded."; Stop-Process -Id $proc.Id -Force; exit 2 }
Start-Sleep -Seconds 3   # let the window settle (maximize etc.)
$proc.Refresh()
$s = Read-State
Write-Host "Before typing: $($s | ConvertTo-Json -Compress)"
Write-Host "Foreground window is ClipTool: $([U32]::GetForegroundWindow() -eq $proc.MainWindowHandle)"

# 1) type straight away, like a user who starts typing as soon as the window appears
[System.Windows.Forms.SendKeys]::SendWait("ab")
Start-Sleep -Seconds 1
$noClick = (Read-State).value

# 2) click in the name box with the mouse, then type
$pt = New-Object POINT
[void][U32]::ClientToScreen($proc.MainWindowHandle, [ref]$pt)
$x = [int]($pt.X + $s.rect.x * $s.dpr); $y = [int]($pt.Y + $s.rect.y * $s.dpr)
Write-Host "Clicking the name box at $x,$y (window client origin $($pt.X),$($pt.Y))"
[void][U32]::SetCursorPos($x, $y)
[U32]::mouse_event(0x2, 0, 0, 0, [UIntPtr]::Zero); [U32]::mouse_event(0x4, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 700
[System.Windows.Forms.SendKeys]::SendWait("^a")
[System.Windows.Forms.SendKeys]::SendWait("hello")
Start-Sleep -Seconds 1
$after = Read-State
Shot "$Name.png"
Write-Host "Typed without clicking: '$noClick'.  After clicking and typing: '$($after.value)'  ($($after | ConvertTo-Json -Compress -Depth 4))"

# 3) press Create, then type into the project name at the top (worked for the user)
function Click($r) {
  $cx = [int]($pt.X + $r.x * $s.dpr); $cy = [int]($pt.Y + $r.y * $s.dpr)
  [void][U32]::SetCursorPos($cx, $cy)
  [U32]::mouse_event(0x2, 0, 0, 0, [UIntPtr]::Zero); [U32]::mouse_event(0x4, 0, 0, 0, [UIntPtr]::Zero)
}
Click $after.create
Start-Sleep -Seconds 3
$st = Read-State
Write-Host "After Create: modal open = $($st.modal), header = '$($st.header)'"
if ($st.headerRect) {
  Click $st.headerRect; Start-Sleep -Milliseconds 600
  [System.Windows.Forms.SendKeys]::SendWait("{END}xyz"); Start-Sleep -Seconds 1
  $st = Read-State
  Write-Host "Header after typing: '$($st.header)'  keys seen by the page: $($st.keys | ConvertTo-Json -Compress)"
}
Stop-Process -Id $proc.Id -Force
if ($after.value -eq "hello" -and $noClick -eq "ab") { Write-Host "RESULT ${Name}: typing works"; exit 0 }
Write-Host "RESULT ${Name}: typing does NOT work"
exit 1
