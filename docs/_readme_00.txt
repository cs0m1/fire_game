This is where the important things to read will come... :)

gRPC server endpoint will be: https://10.x.y.z:5000

*** current damage/sight/HP values:

fire: HP starts at 1000, currently does not cause damage to units (this might change! If you (the players) are good, then we will spice the game up by enabling the fire-damage).

firefighter: HP starts at 1000, damages 50, sight distance = 2 cells, infinite water, speed: 50
truck: HP starts at 1500, damages 200, sight distance = 8 cells, water limited: 20 water units, speed: 100
drone: HP starts at 2000, damages 100, sight distance = 16 cells, water limited: 5 water units, speed: 200

*** the GRPC PROTO file is: 

	syntax = "proto3";

	option csharp_namespace = "FireRa.Service.Grpc";

	package raservice;

	service FireRaService {
	  rpc SayHello (HelloRequest) returns (HelloReply);
	  rpc CommunicateWithStreams (stream CommandMessage) returns (stream CommandMessage);
	}

	message HelloRequest
	{
		string teamName = 1; 
	}

	message HelloReply
	{
		string message = 1;
	}

	message CommandMessage {
	  string teamName = 1;
	  int32 counter = 2;
	  uint32 unitId = 3;
	  string operation = 4;
	  string extraJson = 5;
	}

*** Possible operations (the "operation") field of the commands:

    public enum OperationId
    {
        NOP, /* do nothing */
		ACK, /* Server sends, acknowledges that a command with a counter value has been processed. */ 
        Up, /* move unit */ 
        Left, /* move unit */
		Right, /* move unit */
		Down, /* move unit */
		ExtinguishFire, /* try to put out the fire */
		RefillWithWater, /* try to refill */
		InformationFromServer, /* Server sends, information/error */ 
		UnitsFromServer, /* Server sends, units json. This contain all data for all your units */
	}
