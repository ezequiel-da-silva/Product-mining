# Inicia ou pausa a varredura da Biblioteca de Anúncios.
# O extrator só lê a primeira aba do Chrome na porta 9222.
param(
    [Parameter(ParameterSetName = 'Iniciar', Mandatory = $true)]
    [string]$Term,

    [Parameter(ParameterSetName = 'Pausar', Mandatory = $true)]
    [switch]$Pausar
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Test-PortaAberta {
    param([string]$Endereco, [int]$Porta)
    $cliente = New-Object System.Net.Sockets.TcpClient
    try {
        $espera = $cliente.BeginConnect($Endereco, $Porta, $null, $null)
        $aberta = $espera.AsyncWaitHandle.WaitOne(1500, $false)
        if (-not $aberta) {
            return $false
        }
        $cliente.EndConnect($espera)
        return $true
    } catch {
        return $false
    } finally {
        $cliente.Close()
    }
}

function Get-IpDaPonte {
    $ip = Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $ip) {
        throw 'Adaptador vEthernet (WSL) não encontrado. Abra o Rancher Desktop e tente de novo.'
    }
    return $ip
}

function Get-ChromeExe {
    $candidatos = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($caminho in $candidatos) {
        if ($caminho -and (Test-Path -LiteralPath $caminho)) {
            return $caminho
        }
    }
    $comando = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($comando) {
        return $comando.Source
    }
    throw 'chrome.exe não encontrado.'
}

function Test-PerfilDeMineracaoAberto {
    $processos = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue
    foreach ($processo in $processos) {
        if ($processo.CommandLine -and $processo.CommandLine -like '*chrome-dev-profile*') {
            return $true
        }
    }
    return $false
}

function Get-UrlBiblioteca {
    param([string]$Termo)
    $limpo = $Termo.Trim()
    if (-not $limpo) {
        throw 'Informe um termo de busca.'
    }
    $q = [Uri]::EscapeDataString('"' + $limpo + '"')
    $sufixo = '&search_type=keyword_exact_phrase&sort_data[direction]=desc&sort_data[mode]=total_impressions'
    return 'https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=BR&is_targeted_country=false&media_type=all&q=' + $q + $sufixo
}

function Stop-Produtor {
    Write-Output 'Pausando o extrator.'
    docker compose stop ad-producer
    if ($LASTEXITCODE -ne 0) {
        throw 'Não foi possível parar o ad-producer. O Rancher Desktop está aberto?'
    }
}

function Start-Produtor {
    Write-Output 'Iniciando o extrator.'
    docker compose up -d --no-deps ad-producer
    if ($LASTEXITCODE -ne 0) {
        throw 'Não foi possível subir o ad-producer.'
    }
}

function Update-CdpBridgeIp {
    param([string]$Ip)
    $caminho = Join-Path $root '.env'
    $bytes = [IO.File]::ReadAllBytes($caminho)
    $encoding = New-Object System.Text.UTF8Encoding $false
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 255 -and $bytes[1] -eq 254) {
        $encoding = New-Object System.Text.UnicodeEncoding $false, $true
    } elseif ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {
        $encoding = New-Object System.Text.UTF8Encoding $true
    }
    $texto = $encoding.GetString($bytes)
    $linha = "CDP_BRIDGE_IP=$Ip"
    if ($texto -match '(?m)^CDP_BRIDGE_IP=(.*)$') {
        if ($Matches[1].Trim() -eq $Ip) {
            return $false
        }
        $texto = [regex]::Replace($texto, '(?m)^CDP_BRIDGE_IP=.*$', $linha)
    } else {
        if (-not $texto.EndsWith("`n")) {
            $texto += "`r`n"
        }
        $texto += "$linha`r`n"
    }
    [IO.File]::WriteAllText($caminho, $texto, $encoding)
    return $true
}

function Get-Abas {
    # O ConvertFrom-Json do Windows PowerShell 5.1 funde várias abas num objeto só.
    # O serializador do .NET devolve uma aba por item.
    $resposta = Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/list' -UseBasicParsing -TimeoutSec 5
    Add-Type -AssemblyName System.Web.Extensions
    $serial = New-Object System.Web.Script.Serialization.JavaScriptSerializer
    $serial.MaxJsonLength = 67108864
    $alvos = $serial.DeserializeObject($resposta.Content)
    $paginas = foreach ($alvo in $alvos) {
        if ([string]$alvo['type'] -ne 'page') {
            continue
        }
        $ws = [string]$alvo['webSocketDebuggerUrl']
        if ($ws -notmatch '^ws://127\.0\.0\.1:9222/devtools/page/[A-Fa-f0-9]+$') {
            continue
        }
        [pscustomobject]@{
            id = [string]$alvo['id']
            type = 'page'
            url = [string]$alvo['url']
            webSocketDebuggerUrl = $ws
        }
    }
    return @($paginas)
}

function Close-AbasDeDepuracao {
    foreach ($aba in (Get-Abas)) {
        if ($aba.url -match '://(127\.0\.0\.1|localhost):9222') {
            try {
                Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/close/$($aba.id)" -UseBasicParsing -TimeoutSec 5 | Out-Null
            } catch {
                Write-Output "Não fechei a aba $($aba.id)."
            }
        }
    }
}

function Invoke-Cdp {
    param(
        [string]$WsUrl,
        [string]$Method,
        [hashtable]$Params,
        [int]$TimeoutMs = 20000
    )
    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $cts = New-Object System.Threading.CancellationTokenSource
    $cts.CancelAfter($TimeoutMs)
    try {
        $ws.ConnectAsync([Uri]$WsUrl, $cts.Token).GetAwaiter().GetResult() | Out-Null
        $payload = @{ id = 1; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 6
        $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
        $envio = New-Object 'System.ArraySegment[byte]' -ArgumentList (, $bytes)
        $ws.SendAsync($envio, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cts.Token).GetAwaiter().GetResult() | Out-Null
        $buffer = New-Object byte[] 1048576
        $limite = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
        while ([DateTime]::UtcNow -lt $limite) {
            $fluxo = New-Object System.IO.MemoryStream
            do {
                $recebido = New-Object 'System.ArraySegment[byte]' -ArgumentList (, $buffer)
                $resultado = $ws.ReceiveAsync($recebido, $cts.Token).GetAwaiter().GetResult()
                if ($resultado.Count -gt 0) {
                    $fluxo.Write($buffer, 0, $resultado.Count)
                }
            } while (-not $resultado.EndOfMessage)
            $texto = [Text.Encoding]::UTF8.GetString($fluxo.ToArray())
            if ($texto -match '"id"\s*:\s*1\b') {
                return $texto
            }
        }
        throw 'O Chrome não respondeu ao CDP.'
    } finally {
        try {
            if ($ws.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
                $ws.CloseAsync(
                    [System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
                    'ok',
                    [Threading.CancellationToken]::None
                ).GetAwaiter().GetResult() | Out-Null
            }
        } catch {
        }
        $ws.Dispose()
        $cts.Dispose()
    }
}

function Get-TextoDaAba {
    param([string]$WsUrl)
    $expressao = @'
(() => {
  const t = (document.body && document.body.innerText) || "";
  const m = t.match(/~?\d[\d\.\s]*\s+resultados?/i);
  return JSON.stringify({ len: t.length, match: m ? m[0] : null });
})()
'@
    $bruto = Invoke-Cdp -WsUrl $WsUrl -Method 'Runtime.evaluate' -Params @{
        expression = $expressao
        returnByValue = $true
    }
    $trecho = $null
    if ($bruto -match '\\"match\\":\\"([^\\"]*)\\"') {
        $trecho = $Matches[1]
    } elseif ($bruto -match '"match":"([^"\\]*)"') {
        $trecho = $Matches[1]
    }
    $len = 0
    if ($bruto -match '\\"len\\":(\d+)') {
        $len = [int]$Matches[1]
    } elseif ($bruto -match '"len":(\d+)') {
        $len = [int]$Matches[1]
    }
    return @{ Len = $len; Match = $trecho }
}

function Wait-Resultados {
    param([string]$WsUrl)
    foreach ($tentativa in 1..2) {
        $limite = (Get-Date).AddSeconds(25)
        while ((Get-Date) -lt $limite) {
            Start-Sleep -Seconds 2
            $estado = Get-TextoDaAba -WsUrl $WsUrl
            if ($estado.Match) {
                Write-Output "Biblioteca pronta: $($estado.Match)"
                return
            }
        }
        if ($tentativa -eq 1) {
            Write-Output 'A página ainda não mostrou resultados. Recarregando a aba.'
            Invoke-Cdp -WsUrl $WsUrl -Method 'Page.reload' -Params @{ ignoreCache = $false } | Out-Null
        }
    }
    throw 'A Biblioteca não mostrou resultados. Confira o login do Facebook nesse perfil do Chrome.'
}

function Wait-CdpChrome {
    foreach ($i in 1..20) {
        if (Test-PortaAberta -Endereco '127.0.0.1' -Porta 9222) {
            try {
                Invoke-RestMethod -Uri 'http://127.0.0.1:9222/json/version' -TimeoutSec 3 | Out-Null
                return
            } catch {
            }
        }
        Start-Sleep -Seconds 1
    }
    throw 'O Chrome não abriu a porta 9222. Feche o Chrome que usa C:\chrome-dev-profile e rode de novo.'
}

function Start-ChromeDeMineracao {
    param([string]$Url)
    if (Test-PortaAberta -Endereco '127.0.0.1' -Porta 9222) {
        Write-Output 'Chrome de depuração já está na porta 9222.'
        return
    }
    if (Test-PerfilDeMineracaoAberto) {
        throw 'O perfil C:\chrome-dev-profile está aberto sem a porta 9222. Feche esse Chrome e rode de novo.'
    }
    $chrome = Get-ChromeExe
    Write-Output 'Abrindo o Chrome de depuração.'
    Start-Process -FilePath $chrome -ArgumentList @(
        '--remote-debugging-port=9222',
        '--user-data-dir=C:\chrome-dev-profile',
        $Url
    ) | Out-Null
    Wait-CdpChrome
}

function Start-PonteSePreciso {
    $ip = Get-IpDaPonte
    $mudou = Update-CdpBridgeIp -Ip $ip
    if ($mudou) {
        Write-Output "CDP_BRIDGE_IP atualizado para $ip."
    }
    if (Test-PortaAberta -Endereco $ip -Porta 9222) {
        Write-Output "Ponte CDP já escuta em ${ip}:9222."
        return
    }
    $ponte = Join-Path $PSScriptRoot 'cdp-bridge.ps1'
    Write-Output "Abrindo a ponte CDP em ${ip}:9222."
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ponte
    ) -WindowStyle Hidden | Out-Null
    foreach ($i in 1..15) {
        Start-Sleep -Seconds 1
        if (Test-PortaAberta -Endereco $ip -Porta 9222) {
            return
        }
    }
    throw 'A ponte CDP não passou a escutar.'
}

function Wait-ModoStealth {
    $limite = (Get-Date).AddSeconds(75)
    while ((Get-Date) -lt $limite) {
        $logs = docker compose logs --since 90s ad-producer 2>&1 | Out-String
        if ($logs -match 'Modo Stealth ativado') {
            Write-Output 'Varredura no ar: Modo Stealth ativado.'
            return
        }
        Start-Sleep -Seconds 3
    }
    throw 'O extrator subiu, mas o log não mostrou Modo Stealth ativado.'
}

if ($MyInvocation.InvocationName -eq '.') {
    return
}

if ($Pausar) {
    Stop-Produtor
    Write-Output 'Varredura pausada. O Chrome, a ponte e os workers continuam no ar.'
    exit 0
}

$urlBiblioteca = Get-UrlBiblioteca -Termo $Term
Stop-Produtor
Start-ChromeDeMineracao -Url $urlBiblioteca
Start-PonteSePreciso
Close-AbasDeDepuracao
$abas = @(Get-Abas)
if ($abas.Count -lt 1) {
    throw 'O Chrome não tem uma aba de página para a Biblioteca.'
}
$aba = $abas | Where-Object { $_.url -like 'https://www.facebook.com/ads/library*' } | Select-Object -First 1
if (-not $aba) {
    $aba = $abas | Select-Object -First 1
}
$wsUrl = [string]$aba.webSocketDebuggerUrl
if ($wsUrl -notmatch '^ws://127\.0\.0\.1:9222/devtools/page/[A-Fa-f0-9]+$') {
    throw 'A primeira aba não devolveu um endereço CDP utilizável.'
}
Write-Output "Abrindo a Biblioteca para: $Term"
Invoke-Cdp -WsUrl $wsUrl -Method 'Page.navigate' -Params @{ url = $urlBiblioteca } | Out-Null
Wait-Resultados -WsUrl $wsUrl
Start-Produtor
Wait-ModoStealth
