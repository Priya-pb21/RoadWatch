$groups = @{
 '--color-primary'            = '#2563eb','#2f6feb','#3b82f6'
 '--color-primary-hover'      = '#1d55c5','#1f5fbf'
 '--color-primary-strong'     = '#174ea6','#173f70','#163b69','#245b91','#24569c','#3972c6'
 '--color-primary-soft'       = '#eaf2ff','#eff6ff','#edf4ff','#e8f0fe','#f0f7ff','#f8fbff','#dbeafe'
 '--color-primary-soft-border'= '#bfdbfe'
 '--color-sidebar'            = '#101c32','#0f172a'
 '--color-sidebar-2'          = '#17233a'
 '--color-sidebar-text'       = '#a9b8ce','#9fb1ca','#9fb2cc','#b7c4d8','#c5d1e0'
 '--color-sidebar-muted'      = '#8193ad'
 '--color-sidebar-border'     = '#26344e'
 '--color-sidebar-active-bg'  = '#1e4f94'
 '--color-sidebar-accent'     = '#78a8ff'
 '--color-media-bg'           = '#0b1220'
 '--color-text'               = '#172033','#17253a','#25364d','#26374d','#27364b','#1e293b'
 '--color-text-2'             = '#34445b','#334155','#43536a','#45556b','#46556b','#475569','#41536c','#344054','#55657a','#475467','#66758a','#667085'
 '--color-text-muted'         = '#6b778c','#718096','#64748b','#7a8798','#7790b1','#8995a6','#8a97a9','#8a96a8','#93a0b0','#96a2b3','#9aa5b4'
 '--color-border'             = '#dce5f0','#e5eaf1','#e6ebf2','#e3e9f1','#e1e7ef','#e2e8f0','#edf0f4','#edf2f7','#dce4ee','#dbe3ed','#e0e7f0'
 '--color-border-strong'      = '#d0d5dd','#cbd5e1','#94a3b8'
 '--color-bg'                 = '#f4f7fb','#eef3f9','#eef2f7','#eef2f6','#eef4fb'
 '--color-surface-2'          = '#f7f9fc','#f8fafc','#fafbfd','#f5f7fa','#f1f5f9'
 '--color-success'            = '#16a34a','#37c98a'
 '--color-success-strong'     = '#15803d','#13734a'
 '--color-success-soft'       = '#eaf8f0','#f0fdf4','#dcfce7'
 '--color-success-border'     = '#bbf7d0'
 '--color-warning'            = '#d97706'
 '--color-warning-strong'     = '#9a620b','#92400e','#78350f'
 '--color-warning-soft'       = '#fff5df','#fffbeb','#fef3c7'
 '--color-warning-border'     = '#fde68a','#fcd34d'
 '--color-danger'             = '#dc2626','#e06b65'
 '--color-danger-strong'      = '#b91c1c','#b42318','#991b1b','#a12b27','#ae4741'
 '--color-danger-soft'        = '#fff0ee','#fef2f2','#fff1f0','#fee2e2'
 '--color-danger-border'      = '#fecaca'
 '--color-critical'           = '#7f1d1d'
 '--color-accent'             = '#0f766e'
 '--color-purple'             = '#7c3aed'
}
$map = @{}
foreach ($k in $groups.Keys) { foreach ($h in $groups[$k]) { $map[$h] = $k } }

$ev = [System.Text.RegularExpressions.MatchEvaluator]{
  param($m)
  $key = $m.Value.ToLower()
  if ($map.ContainsKey($key)) { "var($($map[$key]))" } else { $m.Value }
}

$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($f in 'src\App.css','src\index.css','src\App.jsx') {
  $path = (Resolve-Path $f).Path
  Copy-Item $path "$path.bak" -Force
  $t = [IO.File]::ReadAllText($path)
  $t = $t -replace 'solid#','solid #'
  # white background -> surface, white text -> on-primary
  $t = [regex]::Replace($t,'(?<![-\w])(background(?:-color)?\s*:\s*)#(?:fff|ffffff)\b','${1}var(--color-surface)')
  $t = [regex]::Replace($t,'(?<![-\w])(color\s*:\s*)#(?:fff|ffffff)\b','${1}var(--color-on-primary)')
  # everything else via the map
  $t = [regex]::Replace($t,'#[0-9a-fA-F]{3,8}\b',$ev)
  [IO.File]::WriteAllText($path,$t,$utf8)
  Write-Host "Done: $f"
}