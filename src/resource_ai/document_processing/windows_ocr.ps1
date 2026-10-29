$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
function AwaitResult($operation, $resultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
        $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait()
    return $task.Result
}
try {
    $memory = [System.IO.MemoryStream]::new()
    [Console]::OpenStandardInput().CopyTo($memory)
    $memory.Position = 0
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($memory)
    $decoder = AwaitResult ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if ([long]$decoder.PixelWidth * [long]$decoder.PixelHeight -gt 16000000 -or $decoder.PixelWidth -gt 4096 -or $decoder.PixelHeight -gt 4096) { throw 'image-size-limit' }
    $bitmap = AwaitResult ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko'))
    if ($null -eq $engine) { throw 'language-not-installed' }
    $result = AwaitResult ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    $words = @()
    foreach ($line in $result.Lines) {
        $lines += @{content=$line.Text}
        foreach ($word in $line.Words) {
            $rect=$word.BoundingRect
            $words += @{content=$word.Text; confidence=$null; polygon=@($rect.X,$rect.Y,($rect.X+$rect.Width),$rect.Y,($rect.X+$rect.Width),($rect.Y+$rect.Height),$rect.X,($rect.Y+$rect.Height))}
        }
    }
    $text = ($lines | ForEach-Object {$_.content}) -join "`n"
    @{status='extracted'; provider='windows-local-ocr'; external_transmission=$false; pages=@(@{page=1;text=$text;width=$decoder.PixelWidth;height=$decoder.PixelHeight;unit='pixel';lines=$lines;words=$words});tables=@();confidence_available=$false;quality_flags=@('local_ocr_no_table_structure_or_confidence')} | ConvertTo-Json -Depth 12 -Compress
} catch {
    @{status='local_ocr_unavailable_or_invalid_image';provider='windows-local-ocr';pages=@();tables=@()} | ConvertTo-Json -Compress
}
