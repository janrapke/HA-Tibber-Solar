# Estimated grid draw of the Surface Pro 9 for Home Assistant (HASS.Agent PowerShell sensor, every 60 s).
# Prints one number in watts. ASCII only (Windows PowerShell 5.1).
#
# Rules:
#  - not on the home network (WLAN SSID / gateway)   -> 0   (used elsewhere, not our electricity)
#  - on battery                                      -> 0   (no grid draw)
#  - on the charger: (charge power + own use) / charger efficiency
#
# Own use calibrated on battery on 2026-11-05 (Surface Pro 9, 12 logical cores):
#   brightness 50 % idle 6.8 W, 100 % idle 9.4 W  -> screen ~0.052 W per % brightness, base ~4.2 W
#   CPU (brightness ~42 %): 13.5 % -> 7.3 W, 38 % -> 14.4 W, 64 % -> 18.6 W, 100 % -> 19.2 W (battery power limit)
#   On the charger the power limit is higher: 100 % assumed 25 W total.
# Charger efficiency (EU/US Level VI minimum for this size, no measured value for the original charger):
#   < 10 W: 80 %, 10-25 W: 85 %, > 25 W: 88 %

$HOME_SSID = 'FRITZ!Box 6660 Cable QM'
$HOME_GATEWAY = '192.168.178.1'

function Interp($x, $pts) {
  if ($x -le $pts[0][0]) { return $pts[0][1] }
  for ($i = 1; $i -lt $pts.Count; $i++) {
    if ($x -le $pts[$i][0]) {
      $a = $pts[$i - 1]; $b = $pts[$i]
      return $a[1] + ($b[1] - $a[1]) * ($x - $a[0]) / ($b[0] - $a[0])
    }
  }
  return $pts[-1][1]
}

try {
  # 1) at home?
  $ssid = ((netsh wlan show interfaces) -match '^\s+SSID\s+:' | Select-Object -First 1) -replace '^\s+SSID\s+:\s*', ''
  $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
  if ($ssid.Trim() -ne $HOME_SSID -and $gw -ne $HOME_GATEWAY) { '0'; exit }

  # 2) on the charger?
  $bat = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus | Select-Object -First 1
  if (-not $bat.PowerOnline) { '0'; exit }

  # 3) own use from CPU load and brightness
  $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
  try { $bright = (Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness } catch { $bright = 50 }
  # CPU share above the idle base (measured at ~42 % brightness, base there 4.2 + 0.052*42 = 6.4 W)
  $cpuW = Interp $cpu @(@(0, 0.0), @(13.5, 0.9), @(38, 8.0), @(64, 12.2), @(100, 18.6))
  $own = 4.2 + 0.052 * $bright + $cpuW

  # 4) plus what goes into the battery, then the charger loss
  $dc = $own + [math]::Max(0, $bat.ChargeRate / 1000.0)
  $eff = if ($dc -lt 10) { 0.80 } elseif ($dc -lt 25) { 0.85 } else { 0.88 }
  [math]::Round($dc / $eff, 1)
} catch {
  '0'
}
