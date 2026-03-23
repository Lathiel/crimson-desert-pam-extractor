param([string]$mode, [string]$arg1, [string]$arg2)

if ($mode -eq 'dds') {
    # arg1 = PAM stem (no extension), arg2 = source directory (may have trailing \)
    # Finds DDS files where the DDS basename is a prefix of the PAM stem
    # OR the PAM stem is a prefix of the DDS basename (handles _n, _s, _ao etc.)
    $pamLower = $arg1.ToLower()
    $arg2 = $arg2.TrimEnd('\','/','"')
    if (-not (Test-Path -LiteralPath $arg2)) { exit 0 }
    Get-ChildItem -LiteralPath $arg2 -Filter '*.dds' | Where-Object {
        $ddsLower = $_.BaseName.ToLower()
        $pamLower.StartsWith($ddsLower) -or $ddsLower.StartsWith($pamLower)
    } | ForEach-Object { Write-Output $_.FullName }
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms

if ($mode -eq 'folder') {
    $d = [System.Windows.Forms.FolderBrowserDialog]::new()
    $d.Description = 'Select folder containing .pam files'
    $d.ShowNewFolderButton = $false
    if ($d.ShowDialog() -eq 'OK') {
        Write-Output $d.SelectedPath
    } else {
        Write-Output 'CANCELLED'
    }
} else {
    $d = [System.Windows.Forms.OpenFileDialog]::new()
    $d.Title = 'Select PAM file'
    $d.Filter = 'PAM files (*.pam)|*.pam|All files (*.*)|*.*'
    $d.Multiselect = $false
    if ($d.ShowDialog() -eq 'OK') {
        Write-Output $d.FileName
    } else {
        Write-Output 'CANCELLED'
    }
}
