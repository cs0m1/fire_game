"""
Connects directly to the game server and prints everything it sends.
Run: venv/bin/python snoop_server.py
"""
import grpc
import json
import sys
import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER   = "10.4.4.59:5001"
TEAM     = "Prometheus"          # <-- change to your team name


def generate_nops():
    """Keep sending NOPs so the server stays happy and keeps streaming."""
    counter = 0
    while True:
        counter += 1
        yield fire_ra_pb2.CommandMessage(
            teamName=TEAM,
            counter=counter,
            unitId=0,
            operation="NOP",
            extraJson="",
        )


def main():
    print(f"Connecting to {SERVER} as team '{TEAM}' ...")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    # Say hello first
    try:
        reply = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"SayHello response: {reply.message}\n")
    except grpc.RpcError as e:
        print(f"SayHello failed: {e.details()} — continuing anyway\n")

    print("=== Streaming (Ctrl+C to stop) ===\n")
    try:
        for msg in stub.CommunicateWithStreams(generate_nops()):
            op = msg.operation
            print(f"[#{msg.counter}] operation={op!r}  unitId={msg.unitId}")
            if msg.extraJson:
                try:
                    parsed = json.loads(msg.extraJson)
                    print(json.dumps(parsed, indent=2))
                except json.JSONDecodeError:
                    print(f"  raw extraJson: {msg.extraJson}")
            print()
    except grpc.RpcError as e:
        print(f"\nStream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
