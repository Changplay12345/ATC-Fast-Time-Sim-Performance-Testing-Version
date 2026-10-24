param([string]$Deck, [string]$OutDir)
$app = New-Object -ComObject PowerPoint.Application
$p = $app.Presentations.Open($Deck, $true, $false, $false)
for ($i = 1; $i -le $p.Slides.Count; $i++) { $p.Slides.Item($i).Export((Join-Path $OutDir ("slide" + $i + ".png")), "PNG", 1920, 1080) }
$p.Close(); $app.Quit()
Write-Output ("exported " + $p.Slides.Count)
