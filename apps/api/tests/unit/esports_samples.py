"""Complete samples owned by the new module; never imports legacy talent helpers."""

from app.esports_assessments.catalog import recognition_schedule


def sample_raw(suite_id: str, test_id: str, *, perfect: bool, seed: int = 123) -> dict:
    if test_id in ("precision", "switching"):
        count, window = (30, 2000) if test_id == "precision" else (40, 1500)
        return {
            "kind": "aim",
            "trials": [
                {"hit": perfect, "latency_ms": 100 if perfect else window} for _ in range(count)
            ],
            "shots": count if perfect else 0,
            "wrong_target_shots": 0,
        }
    if test_id == "tracking":
        return {
            "kind": "tracking",
            "coverage_ms": 60000 if perfect else 0,
            "loss_count": 0,
            "reacquisition_ms": [],
        }
    if test_id == "recognition":
        return {
            "kind": "recognition",
            "trials": [
                {
                    "target_kind": kind,
                    "shot": perfect and kind == "enemy",
                    "hit": perfect and kind == "enemy",
                    "latency_ms": 100 if perfect and kind == "enemy" else 1200,
                }
                for kind in recognition_schedule(seed)
            ],
        }
    if test_id == "move-fire":
        return {
            "kind": "move_fire",
            "rounds": [{"hit": perfect, "reached": perfect} for _ in range(20)],
            "shots": 20 if perfect else 0,
        }
    if test_id == "last-hit":
        return {
            "kind": "last_hit",
            "waves": [
                {"success": perfect, "early": 0, "late": 0, "offset_ms": 0 if perfect else None}
                for _ in range(15)
            ],
        }
    if test_id in ("orb-walk", "move-attack"):
        return {
            "kind": "rhythm",
            "rounds": [
                {"attacked": perfect, "reached": perfect, "cancelled_count": 0} for _ in range(30)
            ],
        }
    if test_id == "dodge":
        return {
            "kind": "dodge",
            "threats": [
                {"hit": not perfect, "response_ms": 100 if perfect else None} for _ in range(30)
            ],
        }
    if test_id == "skill-aim":
        return {
            "kind": "skill_aim",
            "trials": [
                {
                    "released": perfect,
                    "hit": perfect,
                    "angle_error_deg": 0 if perfect else None,
                    "release_latency_ms": 100 if perfect else None,
                    "hit_latency_ms": 300 if perfect else None,
                }
                for _ in range(30)
            ],
        }
    if test_id == "map-awareness":
        return {
            "kind": "map",
            "main_hits": [perfect] * 30,
            "events": [
                {
                    "response": "correct" if perfect else "missed",
                    "latency_ms": 100 if perfect else 1000,
                }
                for _ in range(12)
            ],
            "unprompted_count": 0,
        }
    raise ValueError(f"unknown sample: {suite_id}/{test_id}")
