# Encaminha o CDP do Chrome (127.0.0.1:9222) para o adaptador virtual do WSL,
# que é o único endereço que os contêineres do Rancher Desktop conseguem alcançar.
$ErrorActionPreference = 'Stop'

$listenIp = (
    Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 |
        Select-Object -First 1 -ExpandProperty IPAddress
)
if (-not $listenIp) {
    throw 'Adaptador vEthernet (WSL) não encontrado. Abra o Rancher Desktop e tente de novo.'
}

Add-Type -TypeDefinition @"
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;

public static class CdpBridge {
    public static void Run(string listenIp, int port) {
        var listener = new TcpListener(IPAddress.Parse(listenIp), port);
        listener.Start();
        Console.WriteLine("CDP bridge " + listenIp + ":" + port + " -> 127.0.0.1:" + port);
        while (true) {
            var client = listener.AcceptTcpClient();
            ThreadPool.QueueUserWorkItem(_ => Handle(client, port));
        }
    }

    static void Handle(TcpClient client, int port) {
        TcpClient upstream = null;
        try {
            upstream = new TcpClient();
            upstream.Connect(IPAddress.Loopback, port);
            var left = client.GetStream();
            var right = upstream.GetStream();
            var toUpstream = new Thread(() => Pump(left, right));
            var toClient = new Thread(() => Pump(right, left));
            toUpstream.IsBackground = true;
            toClient.IsBackground = true;
            toUpstream.Start();
            toClient.Start();
            toUpstream.Join();
            toClient.Join();
        } catch (Exception ex) {
            Console.WriteLine(ex.Message);
        } finally {
            try { client.Close(); } catch { }
            if (upstream != null) {
                try { upstream.Close(); } catch { }
            }
        }
    }

    static void Pump(NetworkStream source, NetworkStream destination) {
        var buffer = new byte[16384];
        try {
            int read;
            while ((read = source.Read(buffer, 0, buffer.Length)) > 0) {
                destination.Write(buffer, 0, read);
            }
        } catch { }
        try { destination.Close(); } catch { }
    }
}
"@

Write-Output "CDP_BRIDGE_IP=$listenIp"
[CdpBridge]::Run($listenIp, 9222)
