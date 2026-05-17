"""
Tests for _enforce_safety() in gemini_agents.py.
Run with: python test_enforce_safety.py
"""
from core.gemini_agents import _enforce_safety

# Safety zone covering the top-right quadrant (simulates a hand zone)
HAND_ZONE = {
    "id": "zone_human_01",
    "type": "human_presence",
    "polygon": [
        {"x": 0.60, "y": 0.00},
        {"x": 1.00, "y": 0.00},
        {"x": 1.00, "y": 0.50},
        {"x": 0.60, "y": 0.50},
    ],
}

ROBOT_BASE_ZONE = {
    "id": "zone_robot_base",
    "type": "robot_base",
    "polygon": [
        {"x": 0.05, "y": 0.00},
        {"x": 0.31, "y": 0.00},
        {"x": 0.31, "y": 0.26},
        {"x": 0.05, "y": 0.26},
    ],
}

SAFETY_ZONES = [HAND_ZONE, ROBOT_BASE_ZONE]


def make_plan(steps):
    return {"strategy": "test", "reasoning": "", "clusters": [], "sequence": steps}


def test_object_inside_zone_gets_skipped():
    """
    Object whose centroid is inside a safety zone should be skipped —
    the robot cannot safely reach into an active human/base zone to pick it up.
    (pen near robot base, glasses/adapter near human hand)
    """
    plan = make_plan([{
        "step": 1,
        "action": "pick_and_place",
        "object_id": "obj_001",
        "from": {"x": 0.75, "y": 0.25},  # inside HAND_ZONE
        "to":   {"x": 0.80, "y": 0.30},  # also inside HAND_ZONE
        "reason": "move glasses",
    }])
    result, relocated = _enforce_safety(plan, SAFETY_ZONES)
    step = result["sequence"][0]
    assert step.get("skip"), "Object inside zone should be skipped"
    assert relocated == 0, f"Expected 0 relocations, got {relocated}"
    print("PASS  test_object_inside_zone_gets_skipped")


def test_object_outside_zone_with_safe_path_unchanged():
    """Object outside all zones with a safe destination should pass through untouched."""
    plan = make_plan([{
        "step": 1,
        "action": "pick_and_place",
        "object_id": "obj_001",
        "from": {"x": 0.40, "y": 0.70},  # safe area
        "to":   {"x": 0.45, "y": 0.80},  # safe area
        "reason": "move book",
    }])
    result, relocated = _enforce_safety(plan, SAFETY_ZONES)
    step = result["sequence"][0]
    assert not step.get("skip"), "Safe object should not be skipped"
    assert relocated == 0, f"Expected 0 relocations, got {relocated}"
    print("PASS  test_object_outside_zone_with_safe_path_unchanged")


def test_object_outside_zone_path_crosses_zone_gets_relocated():
    """Object outside a zone but whose carry path crosses it should be relocated."""
    plan = make_plan([{
        "step": 1,
        "action": "pick_and_place",
        "object_id": "obj_001",
        "from": {"x": 0.40, "y": 0.25},  # outside HAND_ZONE
        "to":   {"x": 0.80, "y": 0.25},  # inside HAND_ZONE — path crosses zone
        "reason": "move pen",
    }])
    result, relocated = _enforce_safety(plan, SAFETY_ZONES)
    step = result["sequence"][0]
    assert not step.get("skip"), "Should find a safe reroute, not skip"
    assert relocated == 1, f"Expected 1 relocation, got {relocated}"
    print("PASS  test_object_outside_zone_path_crosses_zone_gets_relocated")


def test_destination_inside_zone_gets_relocated():
    """Object with a safe from-position but destination inside zone gets relocated."""
    plan = make_plan([{
        "step": 1,
        "action": "pick_and_place",
        "object_id": "obj_001",
        "from": {"x": 0.40, "y": 0.70},  # safe
        "to":   {"x": 0.75, "y": 0.25},  # inside HAND_ZONE
        "reason": "move glasses",
    }])
    result, relocated = _enforce_safety(plan, SAFETY_ZONES)
    step = result["sequence"][0]
    assert not step.get("skip"), "Should relocate destination, not skip"
    assert relocated == 1, f"Expected 1 relocation, got {relocated}"
    print("PASS  test_destination_inside_zone_gets_relocated")


def test_multiple_objects_inside_zone_all_skipped():
    """
    Multiple objects inside safety zones should all be skipped —
    robot cannot pick up from inside an active zone (glasses, adapter, pen).
    """
    plan = make_plan([
        {
            "step": 1, "action": "pick_and_place", "object_id": "obj_001",
            "from": {"x": 0.70, "y": 0.10}, "to": {"x": 0.80, "y": 0.10},
            "reason": "move glasses",
        },
        {
            "step": 2, "action": "pick_and_place", "object_id": "obj_002",
            "from": {"x": 0.65, "y": 0.30}, "to": {"x": 0.75, "y": 0.30},
            "reason": "move adapter",
        },
        {
            "step": 3, "action": "pick_and_place", "object_id": "obj_003",
            "from": {"x": 0.80, "y": 0.40}, "to": {"x": 0.90, "y": 0.40},
            "reason": "move pen",
        },
    ])
    result, relocated = _enforce_safety(plan, SAFETY_ZONES)
    skipped = [s for s in result["sequence"] if s.get("skip")]
    assert len(skipped) == 3, f"Expected 3 skips, got {len(skipped)}"
    assert relocated == 0, f"Expected 0 relocations, got {relocated}"
    print("PASS  test_multiple_objects_inside_zone_all_skipped")


if __name__ == "__main__":
    tests = [
        test_object_inside_zone_gets_skipped,
        test_object_outside_zone_with_safe_path_unchanged,
        test_object_outside_zone_path_crosses_zone_gets_relocated,
        test_destination_inside_zone_gets_relocated,
        test_multiple_objects_inside_zone_all_skipped,
    ]
    failures = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failures.append(t.__name__)
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
