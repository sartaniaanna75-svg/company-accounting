$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:8765/")
$listener.Start()
Write-Output "SERVING http://127.0.0.1:8765/ root=$root"
while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $path = $ctx.Request.Url.LocalPath
    if ($path -eq "/") { $path = "/app.html" }
    $rel = $path.TrimStart("/").Replace("/", "\")
    $file = Join-Path $root $rel
    if (-not (Test-Path -LiteralPath $file)) {
        $ctx.Response.StatusCode = 404
        $ctx.Response.Close()
        continue
    }
    $bytes = [System.IO.File]::ReadAllBytes($file)
    $ext = [System.IO.Path]::GetExtension($file).ToLower()
    $ctype = "application/octet-stream"
    if ($ext -eq ".html") { $ctype = "text/html; charset=utf-8" }
    elseif ($ext -eq ".js") { $ctype = "application/javascript" }
    elseif ($ext -eq ".css") { $ctype = "text/css" }
    $ctx.Response.ContentType = $ctype
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
}
