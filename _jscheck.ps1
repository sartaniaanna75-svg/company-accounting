$path = Join-Path $PSScriptRoot "app.html"
$s = [IO.File]::ReadAllText($path)
$i = $s.IndexOf("<script>")
$j = $s.LastIndexOf("</script>")
$js = $s.Substring($i + 8, $j - $i - 8)
$n = $js.Length
$line = 1
$col = 1
$brace = 0
$paren = 0
$brack = 0
$mode = "code" # code, sq, dq, tmpl, linec, blockc
$tmplDepth = 0
$i2 = 0
$firstNeg = $null
function Pos($l,$c) { return "${l}:${c}" }
while ($i2 -lt $n) {
  $ch = $js[$i2]
  $nx = if ($i2+1 -lt $n) { $js[$i2+1] } else { [char]0 }
  if ($ch -eq "`n") { $line++; $col = 1; $i2++; continue }
  if ($mode -eq "linec") {
    $i2++; $col++; continue
  }
  if ($mode -eq "blockc") {
    if ($ch -eq "*" -and $nx -eq "/") { $mode = "code"; $i2 += 2; $col += 2; continue }
    $i2++; $col++; continue
  }
  if ($mode -eq "sq") {
    if ($ch -eq "\") { $i2 += 2; $col += 2; continue }
    if ($ch -eq "'") { $mode = "code" }
    $i2++; $col++; continue
  }
  if ($mode -eq "dq") {
    if ($ch -eq "\") { $i2 += 2; $col += 2; continue }
    if ($ch -eq '"') { $mode = "code" }
    $i2++; $col++; continue
  }
  if ($mode -eq "tmpl") {
    if ($ch -eq "\") { $i2 += 2; $col += 2; continue }
    if ($ch -eq '`') { $mode = "code"; $i2++; $col++; continue }
    if ($ch -eq '$' -and $nx -eq '{') { $tmplDepth++; $mode = "code"; $i2 += 2; $col += 2; continue }
    $i2++; $col++; continue
  }
  # code
  if ($ch -eq "/" -and $nx -eq "/") { $mode = "linec"; $i2 += 2; $col += 2; continue }
  if ($ch -eq "/" -and $nx -eq "*") { $mode = "blockc"; $i2 += 2; $col += 2; continue }
  if ($ch -eq "'") { $mode = "sq"; $i2++; $col++; continue }
  if ($ch -eq '"') { $mode = "dq"; $i2++; $col++; continue }
  if ($ch -eq '`') { $mode = "tmpl"; $i2++; $col++; continue }
  if ($ch -eq '{') { $brace++ }
  elseif ($ch -eq '}') {
    $brace--
    if ($tmplDepth -gt 0 -and $brace -ge 0) {
      # might be end of ${ }
      # cannot easily know; ignore
    }
    if ($brace -lt 0 -and -not $firstNeg) { $firstNeg = "brace $(Pos $line $col)" }
  }
  elseif ($ch -eq '(') { $paren++ }
  elseif ($ch -eq ')') {
    $paren--
    if ($paren -lt 0 -and -not $firstNeg) { $firstNeg = "paren $(Pos $line $col)" }
  }
  elseif ($ch -eq '[') { $brack++ }
  elseif ($ch -eq ']') {
    $brack--
    if ($brack -lt 0 -and -not $firstNeg) { $firstNeg = "brack $(Pos $line $col)" }
  }
  $i2++; $col++
}
Write-Output "brace=$brace paren=$paren brack=$brack firstNeg=$firstNeg endMode=$mode lines=$line jsLen=$n"
