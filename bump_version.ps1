$f = [System.IO.File]::ReadAllText("watcher\common.py", [System.Text.Encoding]::UTF8)
if ($f -match 'VERSION = "(\d+\.\d+\.\d+)"') {
    $old = $Matches[1]
    $parts = $old.Split(".")
    $parts[2] = [int]$parts[2] + 1
    $new = $parts -join "."
    $f = $f.Replace("VERSION = `"$old`"", "VERSION = `"$new`"")
    [System.IO.File]::WriteAllText("watcher\common.py", $f, (New-Object System.Text.UTF8Encoding $false))
    Write-Output "$old -> $new"
}
