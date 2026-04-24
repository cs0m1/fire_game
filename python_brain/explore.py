"""
Explorer AI - Moves units randomly to discover the map and find fires!
"""
import grpc
import json
import os
import random
import time
import fire_ra_pb2
import fire_ra_pb2_grpc

SERVER   = "10.4.4.59:5001"
TEAM     = "Meow"

# We use a global variable to store the latest map data so our AI thread can read it.
latest_units_data = []

def ai_loop():
    """This is the brain! It reads the latest_units_data and sends commands."""
    global latest_units_data
    counter = 0
    
    while True:
        # If we don't have map data yet, just send NOP
        if not latest_units_data:
            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM, counter=counter, unitId=0, operation="NOP", extraJson=""
            )
            time.sleep(0.5)
            continue

        # If we DO have map data, loop through our units and give them commands!
        for unit in latest_units_data:
            uid = unit["Id"]
            
            # --- SUPER BASIC AI LOGIC ---
            # Pick a random direction to explore
            direction = random.choice(["Up", "Down", "Left", "Right"])
            
            counter += 1
            yield fire_ra_pb2.CommandMessage(
                teamName=TEAM,
                counter=counter,
                unitId=uid,
                operation=direction,
                extraJson=""
            )
            
        # Wait a bit before sending the next batch of commands so we don't crash the server
        time.sleep(0.25)


def main():
    global latest_units_data
    print(f"Connecting to {SERVER} as team '{TEAM}' ...")
    channel = grpc.insecure_channel(SERVER)
    stub    = fire_ra_pb2_grpc.FireRaServiceStub(channel)

    try:
        reply = stub.SayHello(fire_ra_pb2.HelloRequest(teamName=TEAM))
        print(f"SayHello response: {reply.message}\n")
    except grpc.RpcError as e:
        print(f"SayHello failed: {e.details()} — continuing anyway\n")

    print("=== AI Explorer Active ===\n")
    try:
        # We pass our ai_loop() generator to the server
        for msg in stub.CommunicateWithStreams(ai_loop()):
            op = msg.operation
            
            # Ignore ACKs and Server Info to keep the terminal clean
            if op in ["ACK", "InformationFromServer"]:
                continue

            # Handle Map/Unit Data!
            if op == "UnitsFromServer":
                try:
                    # Update our global state so the ai_loop can see it
                    latest_units_data = json.loads(msg.extraJson)
                    
                    # Clear screen for a clean radar view
                    os.system('cls' if os.name == 'nt' else 'clear')
                    print(f"--- RADAR ACTIVE ---")
                    
                    # Check if ANY unit sees a fire!
                    found_fire = False
                    for unit in latest_units_data:
                        unit_type = unit["UnitType"]
                        uid = unit["Id"]
                        x, y = unit["Position"]["X"], unit["Position"]["Y"]
                        
                        print(f"[{unit_type} {uid}] at X:{x}, Y:{y} (Water: {unit['CurrentWaterLevel']})")
                        
                        if len(unit["SeenFires"]) > 0:
                            found_fire = True
                            print(f"  🚨 {unit_type} SEES FIRE: {unit['SeenFires']}")
                        
                        if len(unit["SeenWaters"]) > 0:
                            print(f"  💧 {unit_type} SEES WATER: {unit['SeenWaters']}")
                            
                    if found_fire:
                        print("\n🔥🔥🔥 FIRE DETECTED ON RADAR! 🔥🔥🔥")
                        # Save the json so you can see what the fire data looks like
                        with open("fire_spotted.json", "w") as f:
                            json.dump(latest_units_data, f, indent=2)
                    
                except json.JSONDecodeError:
                    pass

    except grpc.RpcError as e:
        print(f"\nStream ended: {e.details()}")
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()