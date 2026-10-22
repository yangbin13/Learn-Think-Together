param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RecordingFolder = 'data/release-recording-20261011'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$recordingPath = Join-Path $ProjectRoot $RecordingFolder
$storyboard = Get-Content -LiteralPath (Join-Path $recordingPath 'storyboard.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$speechEngine = New-Object System.Speech.Synthesis.SpeechSynthesizer
$chineseVoice = $speechEngine.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-CN*' } | Select-Object -First 1
if (-not $chineseVoice) { throw 'An installed Chinese SAPI voice is required.' }
$speechEngine.SelectVoice($chineseVoice.VoiceInfo.Name)
$speechEngine.Rate = 1
$speechEngine.Volume = 100
$manifest = @()
try {
    for ($clipIndex = 0; $clipIndex -lt $storyboard.scenes.Count; $clipIndex++) {
        $scene = $storyboard.scenes[$clipIndex]
        $waveFile = Join-Path $recordingPath ('narration-{0:D2}.wav' -f $clipIndex)
        $speechEngine.SetOutputToWaveFile($waveFile)
        $speechEngine.Speak([string]$scene.speech)
        $speechEngine.SetOutputToNull()
        $manifest += @{ index=$clipIndex; path=$waveFile; title=$scene.title; voice=$chineseVoice.VoiceInfo.Name }
    }
} finally {
    $speechEngine.Dispose()
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $recordingPath 'narration-clips.json') -Encoding UTF8
Write-Output "NARRATION_CLIPS=$($manifest.Count)"
