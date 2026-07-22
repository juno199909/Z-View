"""
Test WebSocket connection to remote desktop
"""
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:9000/remote-desktop"
    print(f"Connecting to {uri}...")

    try:
        async with websockets.connect(uri, timeout=10) as websocket:
            print("✅ WebSocket connected!")

            # Wait for first frame
            print("Waiting for first frame...")
            message = await asyncio.wait_for(websocket.recv(), timeout=10)

            print(f"✅ Received message, type: {type(message)}, length: {len(message) if isinstance(message, (str, bytes)) else 'N/A'}")

            if isinstance(message, str):
                data = json.loads(message)
                print(f"Message type: {data.get('type')}")
                if data.get('type') == 'frame':
                    print(f"Frame size: {data.get('width')}x{data.get('height')}")
                    print(f"Frame number: {data.get('frame')}")
                    print("✅ Remote desktop is working!")
                else:
                    print(f"Unexpected message: {data}")

    except asyncio.TimeoutError:
        print("❌ Timeout: No frame received within 10 seconds")
        print("Possible causes:")
        print("1. ImageGrab.grab() failed (Win11 compatibility issue)")
        print("2. PIL not installed or broken")
        print("3. Agent doesn't have permission to capture screen")
    except websockets.exceptions.WebSocketException as e:
        print(f"❌ WebSocket error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Testing Remote Desktop WebSocket Connection")
    print("=" * 60)
    asyncio.run(test_websocket())
