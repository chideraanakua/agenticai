/*
 * PipelineReceiver.cs
 * 
 * MINI TEST: Unity side of the Python → Unity pipeline.
 * 
 * SETUP (5 minutes):
 * 1. Create a new Unity 3D project (Unity 2022 LTS)
 * 2. Install NativeWebSocket:
 *    - Window → Package Manager → + → Add package from git URL
 *    - Paste: https://github.com/endel/NativeWebSocket.git#upm
 * 3. Create the scene:
 *    - Add an empty GameObject, name it "PipelineManager"
 *    - Attach this script to it
 *    - Add a Plane at position (0, 0, 0), scale (1.2, 1, 0.8) — this is the "table"
 *    - Set camera to position (0, 8, 0), rotation (90, 0, 0) for top-down view
 * 4. Hit Play, then start server.py, then open http://localhost:8000
 * 
 * The script auto-creates colored cubes from the workspace JSON.
 * When move commands arrive, cubes slide to their target positions.
 */

using System;
using System.Collections.Generic;
using UnityEngine;
using NativeWebSocket;

[Serializable]
public class Vec2 { public float x; public float y; }

[Serializable]
public class WorkspaceObject
{
    public string id;
    public string label;
    public string color;
    public Vec2 position;
}

[Serializable]
public class SafetyZone
{
    public string id;
    public Vec2[] polygon;
}

[Serializable]
public class WorkspaceData
{
    public WorkspaceObject[] objects;
    public SafetyZone[] safety_zones;
}

[Serializable]
public class WorkspaceInit
{
    public string type;
    public WorkspaceData data;
}

[Serializable]
public class MoveCommand
{
    public string type;
    public int step;
    public string object_id;
    public Vec2 target;
    public float speed;
    public string message; // for "done" type
}

public class PipelineReceiver : MonoBehaviour
{
    [Header("Connection")]
    public string serverUrl = "ws://localhost:8000/ws/unity";

    [Header("Workspace")]
    public float tableWidth = 12f;   // Unity units
    public float tableHeight = 8f;
    public float cubeSize = 0.6f;
    public float moveSpeed = 3f;

    private WebSocket ws;
    private Dictionary<string, GameObject> objects = new Dictionary<string, GameObject>();
    private Dictionary<string, Vector3> moveTargets = new Dictionary<string, Vector3>();
    private GameObject safetyZoneVisual;

    // Color map
    private Dictionary<string, Color> colorMap = new Dictionary<string, Color>()
    {
        {"blue",   new Color(0.22f, 0.54f, 0.87f)},
        {"red",    new Color(0.85f, 0.24f, 0.24f)},
        {"orange", new Color(0.85f, 0.62f, 0.15f)},
        {"pink",   new Color(0.83f, 0.33f, 0.50f)},
        {"green",  new Color(0.25f, 0.73f, 0.42f)},
    };

    async void Start()
    {
        Debug.Log("Connecting to " + serverUrl + "...");

        ws = new WebSocket(serverUrl);

        ws.OnOpen += () =>
        {
            Debug.Log("<color=green>✅ Connected to Python server!</color>");
        };

        ws.OnMessage += (bytes) =>
        {
            string msg = System.Text.Encoding.UTF8.GetString(bytes);
            HandleMessage(msg);
        };

        ws.OnError += (e) =>
        {
            Debug.LogError("WebSocket error: " + e);
        };

        ws.OnClose += (code) =>
        {
            Debug.Log("Disconnected from server.");
        };

        await ws.Connect();
    }

    void HandleMessage(string json)
    {
        // Check message type
        if (json.Contains("\"type\":\"workspace_init\"") || json.Contains("\"type\": \"workspace_init\""))
        {
            var init = JsonUtility.FromJson<WorkspaceInit>(json);
            if (init?.data != null)
            {
                CreateWorkspace(init.data);
            }
        }
        else if (json.Contains("\"type\":\"move\"") || json.Contains("\"type\": \"move\""))
        {
            var cmd = JsonUtility.FromJson<MoveCommand>(json);
            if (cmd != null)
            {
                QueueMove(cmd);
            }
        }
        else if (json.Contains("\"type\":\"done\"") || json.Contains("\"type\": \"done\""))
        {
            Debug.Log("<color=green>✅ All moves complete!</color>");
        }
    }

    void CreateWorkspace(WorkspaceData data)
    {
        Debug.Log($"Creating workspace: {data.objects.Length} objects, {data.safety_zones.Length} safety zones");

        // Clear existing
        foreach (var obj in objects.Values)
        {
            Destroy(obj);
        }
        objects.Clear();
        moveTargets.Clear();

        // Create objects as colored cubes
        foreach (var obj in data.objects)
        {
            Vector3 worldPos = NormalizedToWorld(obj.position.x, obj.position.y);

            GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = obj.id + "_" + obj.label;
            cube.transform.position = worldPos;
            cube.transform.localScale = Vector3.one * cubeSize;

            // Set color
            var renderer = cube.GetComponent<Renderer>();
            Color col = colorMap.ContainsKey(obj.color) ? colorMap[obj.color] : Color.gray;
            renderer.material = new Material(Shader.Find("Standard"));
            renderer.material.color = col;

            // Add floating label
            CreateLabel(cube, obj.label);

            objects[obj.id] = cube;
            Debug.Log($"  Created {obj.label} ({obj.color}) at ({obj.position.x:F2}, {obj.position.y:F2})");
        }

        // Create safety zone visualization
        if (data.safety_zones.Length > 0)
        {
            CreateSafetyZone(data.safety_zones[0]);
        }

        // Send ack
        SendAck("workspace_ready");
    }

    void CreateSafetyZone(SafetyZone zone)
    {
        if (safetyZoneVisual != null) Destroy(safetyZoneVisual);

        // Simple red transparent quad for the zone
        safetyZoneVisual = GameObject.CreatePrimitive(PrimitiveType.Quad);
        safetyZoneVisual.name = "SafetyZone_" + zone.id;

        // Calculate bounds from polygon
        float minX = float.MaxValue, maxX = float.MinValue;
        float minY = float.MaxValue, maxY = float.MinValue;
        foreach (var p in zone.polygon)
        {
            minX = Mathf.Min(minX, p.x);
            maxX = Mathf.Max(maxX, p.x);
            minY = Mathf.Min(minY, p.y);
            maxY = Mathf.Max(maxY, p.y);
        }

        Vector3 center = NormalizedToWorld((minX + maxX) / 2f, (minY + maxY) / 2f);
        float width = (maxX - minX) * tableWidth;
        float height = (maxY - minY) * tableHeight;

        safetyZoneVisual.transform.position = new Vector3(center.x, 0.01f, center.z);
        safetyZoneVisual.transform.rotation = Quaternion.Euler(90, 0, 0);
        safetyZoneVisual.transform.localScale = new Vector3(width, height, 1);

        var renderer = safetyZoneVisual.GetComponent<Renderer>();
        renderer.material = new Material(Shader.Find("Standard"));
        renderer.material.color = new Color(1f, 0.2f, 0.2f, 0.25f);
        renderer.material.SetFloat("_Mode", 3); // Transparent
        renderer.material.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
        renderer.material.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
        renderer.material.SetInt("_ZWrite", 0);
        renderer.material.DisableKeyword("_ALPHATEST_ON");
        renderer.material.EnableKeyword("_ALPHABLEND_ON");
        renderer.material.DisableKeyword("_ALPHAPREMULTIPLY_ON");
        renderer.material.renderQueue = 3000;

        Debug.Log($"  Safety zone: x=[{minX:F2},{maxX:F2}] y=[{minY:F2},{maxY:F2}]");
    }

    void CreateLabel(GameObject parent, string text)
    {
        // Create a simple 3D text above the cube
        GameObject labelObj = new GameObject("Label_" + text);
        labelObj.transform.SetParent(parent.transform);
        labelObj.transform.localPosition = new Vector3(0, 0.8f, 0);
        labelObj.transform.rotation = Quaternion.Euler(90, 0, 0); // Face camera (top-down)

        var textMesh = labelObj.AddComponent<TextMesh>();
        textMesh.text = text;
        textMesh.fontSize = 24;
        textMesh.characterSize = 0.15f;
        textMesh.anchor = TextAnchor.MiddleCenter;
        textMesh.alignment = TextAlignment.Center;
        textMesh.color = Color.white;
    }

    void QueueMove(MoveCommand cmd)
    {
        if (!objects.ContainsKey(cmd.object_id))
        {
            Debug.LogWarning($"Object {cmd.object_id} not found!");
            return;
        }

        Vector3 target = NormalizedToWorld(cmd.target.x, cmd.target.y);
        moveTargets[cmd.object_id] = target;

        string objName = objects[cmd.object_id].name;
        Debug.Log($"<color=cyan>→ Step {cmd.step}: Moving {objName} to ({cmd.target.x:F2}, {cmd.target.y:F2})</color>");

        SendAck($"moving_{cmd.object_id}_step_{cmd.step}");
    }

    Vector3 NormalizedToWorld(float nx, float ny)
    {
        // Convert 0-1 coordinates to Unity world space
        // (0,0) = top-left of table, (1,1) = bottom-right
        float x = (nx - 0.5f) * tableWidth;
        float z = (0.5f - ny) * tableHeight;  // Flip Y for Unity's Z axis
        return new Vector3(x, cubeSize / 2f, z);
    }

    void Update()
    {
#if !UNITY_WEBGL || UNITY_EDITOR
        ws?.DispatchMessageQueue();
#endif

        // Smoothly move objects toward targets
        foreach (var kvp in moveTargets)
        {
            if (objects.ContainsKey(kvp.Key))
            {
                var obj = objects[kvp.Key];
                Vector3 current = obj.transform.position;
                Vector3 target = kvp.Value;

                if (Vector3.Distance(current, target) > 0.01f)
                {
                    // Lift → move → lower arc
                    float liftHeight = cubeSize / 2f + 1.5f;
                    Vector3 liftedTarget = new Vector3(target.x, liftHeight, target.z);

                    if (current.y < liftHeight - 0.1f && Vector3.Distance(
                        new Vector3(current.x, 0, current.z),
                        new Vector3(target.x, 0, target.z)) > 0.5f)
                    {
                        // Phase 1: Lift up
                        Vector3 liftPos = new Vector3(current.x, liftHeight, current.z);
                        obj.transform.position = Vector3.MoveTowards(current, liftPos, moveSpeed * Time.deltaTime);
                    }
                    else if (Vector3.Distance(
                        new Vector3(current.x, 0, current.z),
                        new Vector3(target.x, 0, target.z)) > 0.1f)
                    {
                        // Phase 2: Move horizontally (while lifted)
                        Vector3 hoverTarget = new Vector3(target.x, liftHeight, target.z);
                        obj.transform.position = Vector3.MoveTowards(current, hoverTarget, moveSpeed * Time.deltaTime);
                    }
                    else
                    {
                        // Phase 3: Lower down
                        obj.transform.position = Vector3.MoveTowards(current, target, moveSpeed * Time.deltaTime);
                    }
                }
            }
        }
    }

    async void SendAck(string status)
    {
        if (ws?.State == WebSocketState.Open)
        {
            string msg = $"{{\"ack\": true, \"status\": \"{status}\"}}";
            await ws.SendText(msg);
        }
    }

    async void OnDestroy()
    {
        if (ws != null)
        {
            await ws.Close();
        }
    }
}
