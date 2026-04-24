using System;
using System.Threading;
using System.Threading.Tasks;
using Grpc.Core;
using Grpc.Net.Client;
using FireRa.Service.Grpc; // This is generated from your .proto file

namespace Client.UI
{

public enum OperationId
{
    NOP, ACK, Up, Left, Right, Down, 
    ExtinguishFire, RefillWithWater, 
    InformationFromServer, UnitsFromServer
}

public class GameClient : IAsyncDisposable
{
    private readonly GrpcChannel _channel;
    private readonly FireRaService.FireRaServiceClient _client;
    private AsyncDuplexStreamingCall<CommandMessage, CommandMessage>? _stream;
    private CancellationTokenSource _cts = new();

    // Event so your UI or AI can subscribe to incoming server messages
    public event Action<CommandMessage>? OnMessageReceived;

    public GameClient(string serverAddress)
    {
        _channel = GrpcChannel.ForAddress(serverAddress);
        _client = new FireRaService.FireRaServiceClient(_channel);
    }

    public async Task StartGameStreamAsync(string teamName)
    {
        // 1. Initial Handshake
        var helloReply = await _client.SayHelloAsync(new HelloRequest { TeamName = teamName });
        Console.WriteLine($"Server says: {helloReply.Message}");

        // 2. Open the Bidirectional Stream
        _stream = _client.CommunicateWithStreams();

        // 3. Start the background listener loop (fire and forget)
        _ = Task.Run(() => ReceiveLoopAsync(_cts.Token));
    }

    // The background loop that constantly listens for Server updates (like UnitsFromServer)
    private async Task ReceiveLoopAsync(CancellationToken token)
    {
        try
        {
            // MoveNext() waits until the server sends a message
            while (await _stream!.ResponseStream.MoveNext(token))
            {
                var message = _stream.ResponseStream.Current;
                
                // Fire the event to notify the UI/AI
                OnMessageReceived?.Invoke(message);
            }
        }
        catch (RpcException ex) when (ex.StatusCode == StatusCode.Cancelled)
        {
            Console.WriteLine("Stream cancelled.");
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Stream error: {ex.Message}");
        }
    }

    // Method for your AI or UI to call when it wants to make a move
    public async Task SendCommandAsync(string teamName, int counter, uint unitId, OperationId operation, string extraJson = "")
    {
        if (_stream == null) return;

        var command = new CommandMessage
        {
            TeamName = teamName,
            Counter = counter,
            UnitId = unitId,
            Operation = operation.ToString(), // Convert enum to string for the proto
            ExtraJson = extraJson
        };

        // Write to the stream
        await _stream.RequestStream.WriteAsync(command);
    }

    public async ValueTask DisposeAsync()
    {
        _cts.Cancel();
        if (_stream != null)
        {
            await _stream.RequestStream.CompleteAsync();
            _stream.Dispose();
        }
        _channel.Dispose();
    }
}
}
