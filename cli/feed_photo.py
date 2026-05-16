"""
feed_photo.py — Feed a real photo into the running pipeline.

This replaces the hardcoded data in server.py with real detections.
The server must already be running.

Usage:
    python feed_photo.py photo.jpg

What it does:
    1. Runs detect.py on the photo
    2. Sends the workspace JSON to the server via HTTP POST
    3. The server pushes it to Unity over WebSocket
    4. You see real detected objects appear in Unity
"""

import sys
import json
import requests

# Switch between detectors here:
# from detect import detect_objects           # OpenCV (color-based, no API key needed)
from cli.detect import detect_objects_gemini as detect_objects  # Gemini (smarter, needs API key)

def main():
    if len(sys.argv) < 2:
        print("Usage: python feed_photo.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    server_url = "http://localhost:8000"

    # Step 1: Detect objects
    print(f"\n📸 Running detection on: {image_path}")
    workspace, _ = detect_objects(image_path)

    obj_count = len(workspace["workspace"]["objects"])
    zone_count = len(workspace["workspace"]["safety_zones"])
    print(f"   Found {obj_count} objects, {zone_count} safety zones")

    # Step 2: Send to server
    print(f"\n📡 Sending to server at {server_url}...")
    try:
        resp = requests.post(f"{server_url}/api/workspace", json=workspace)
        if resp.status_code == 200:
            print(f"   ✅ Server accepted workspace")
        else:
            print(f"   ❌ Server returned {resp.status_code}: {resp.text}")
            sys.exit(1)
    except requests.ConnectionError:
        print(f"   ❌ Can't connect to server. Is server.py running?")
        sys.exit(1)

    # Step 3: Trigger sorting
    print(f"\n🧠 Requesting sorting plan...")
    resp = requests.post(f"{server_url}/api/plan")
    if resp.status_code == 200:
        plan = resp.json()
        steps = plan.get("sequence", plan.get("plan", {}).get("sequence", []))
        print(f"   ✅ Got plan with {len(steps)} steps")
        for step in steps:
            print(f"      Step {step.get('step', '?')}: {step.get('object_id', '?')} → ({step.get('to', {}).get('x', '?')}, {step.get('to', {}).get('y', '?')})")
    else:
        print(f"   ❌ Plan failed: {resp.text}")

    print(f"\n🤖 Commands sent to Unity. Check the simulation!")


if __name__ == "__main__":
    main()