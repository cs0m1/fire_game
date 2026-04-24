"""
Connects directly to the game server and prints the MAP and UNITS.
"""
import grpc
import json
import sys
import time
import os
import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER   = "10.4.4.59:5001"
TEAM     = "Prometheus"          # <-- Your team name


def generate_nops():
    """Send NOPs to keep the stream alive, but not too fast!"""
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
        # SUPER IMPORTANT: Don't spam the server. Wait 0.5 seconds between NOPs
        time.sleep(0.5)


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

    print("=== Radar Active (Listening for Units & Fire) ===\n")
    try:
        for msg in stub.CommunicateWithStreams(generate_nops()):
            op = msg.operation
            
            # 1. Ignore ACKs to keep terminal clean
            if op == "ACK":
                continue 

            # 2. Handle Server Info
            if op == "InformationFromServer":
                print(f"⚠️ SERVER INFO: {msg.extraJson}")
                continue

            # 3. Handle Map/Unit Data!
            if op == "UnitsFromServer":
                try:
                    map_data = json.loads(msg.extraJson)
                    
                    # Save it to a file so you can look at it in VSCode easily
                    with open("latest_map.json", "w") as f:
                        json.dump(map_data, f, indent=2)
                    
                    # Clear terminal screen (makes it look like a live radar)
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(f"--- LATEST RADAR DATA (Saved to latest_map.json) ---")
                    
                    # Note: We are printing the top level keys. 
                    # Once you run this, you will see exactly how the server structures the (X,Y,HP)
                    print(json.dumps(map_data, indent=4))
                    print("\nWaiting for next update...")
                    
                except json.JSONDecodeError:
                    print(f"Failed to parse map data! Raw: {msg.extraJson}")

    except grpc.RpcError as e:
        print(f"\nStream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()