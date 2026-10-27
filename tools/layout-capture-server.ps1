$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add('http://127.0.0.1:9999/')
$listener.Start()
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
  $context = $listener.GetContext()
  $line = $context.Request.Url.Query
  [System.IO.File]::AppendAllText('c:\dev\Dflash-Console\layout-http.txt', "$line`n")
  $context.Response.StatusCode = 204
  $context.Response.Close()
}
$listener.Stop()
