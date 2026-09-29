"""字幕三方合并 API 的端到端测试（标准库 unittest + FastAPI TestClient）。"""

import unittest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def cue(id, start, end, text="t", speaker=None):
    data = {"id": id, "start": start, "end": end, "text": text}
    if speaker is not None:
        data["speaker"] = speaker
    return data


def anchor(source, target):
    return {"source": source, "target": target}


def selection(start, end):
    return {"start": start, "end": end}


class HealthTests(unittest.TestCase):
    def test_health(self):
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class MergeTests(unittest.TestCase):
    def test_unchanged_three_versions_merge_clean(self):
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 0, 2, "hello")],
            "incoming": [cue("a", 0, 2, "hello")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "merged")
        self.assertEqual(data["count"], 1)

    def test_single_side_change_is_adopted(self):
        # current 单独修改文本；incoming 未动
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 0, 2, "hello!")],
            "incoming": [cue("a", 0, 2, "hello")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["cues"][0]["text"], "hello!")

    def test_both_sides_change_different_fields(self):
        # current 改时间，incoming 改文本：各自采纳
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 1, 3, "hello")],
            "incoming": [cue("a", 0, 2, "你好")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        merged = resp.json()["cues"][0]
        self.assertEqual((merged["start"], merged["end"]), (1, 3))
        self.assertEqual(merged["text"], "你好")

    def test_both_sides_change_to_same_value(self):
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 0, 2, "hi")],
            "incoming": [cue("a", 0, 2, "hi")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["cues"][0]["text"], "hi")

    def test_field_level_conflict_different_values(self):
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 0, 2, "hi")],
            "incoming": [cue("a", 0, 2, "yo")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "conflict")
        self.assertEqual(len(data["field_conflicts"]), 1)
        fc = data["field_conflicts"][0]
        self.assertEqual(fc["cue_id"], "a")
        self.assertEqual(fc["field"], "text")
        self.assertEqual(fc["base"], "hello")
        self.assertEqual(fc["current"], "hi")
        self.assertEqual(fc["incoming"], "yo")

    def test_both_delete_is_clean(self):
        body = {
            "base": [cue("a", 0, 2), cue("b", 3, 4)],
            "current": [cue("b", 3, 4)],
            "incoming": [cue("b", 3, 4)],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([c["id"] for c in resp.json()["cues"]], ["b"])

    def test_delete_vs_modify_is_conflict(self):
        # current 删除 a，incoming 修改 a
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [],
            "incoming": [cue("a", 0, 2, "hello!")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "conflict")
        self.assertEqual(len(data["cue_conflicts"]), 1)
        cc = data["cue_conflicts"][0]
        self.assertEqual(cc["cue_id"], "a")
        self.assertEqual(cc["type"], "delete_modify")
        self.assertEqual(cc["side"], "current")
        self.assertIn("text", cc["fields"])

    def test_delete_vs_unchanged_adopts_delete(self):
        # 一侧删除，另一侧原样保留：删除生效
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [],
            "incoming": [cue("a", 0, 2, "hello")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 0)

    def test_add_add_same_value_adopts(self):
        body = {
            "base": [],
            "current": [cue("n", 5, 7, "new")],
            "incoming": [cue("n", 5, 7, "new")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["cues"][0]["id"], "n")

    def test_add_add_different_value_is_conflict(self):
        body = {
            "base": [],
            "current": [cue("n", 5, 7, "new")],
            "incoming": [cue("n", 5, 7, "brand new")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 409)
        cc = resp.json()["cue_conflicts"][0]
        self.assertEqual(cc["type"], "add_add")
        self.assertEqual(cc["fields"], ["text"])

    def test_result_sorted_by_start(self):
        body = {
            "base": [],
            "current": [cue("c", 10, 11, "late"), cue("a", 0, 1, "early")],
            "incoming": [cue("b", 5, 6, "mid")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [c["id"] for c in resp.json()["cues"]]
        self.assertEqual(ids, ["a", "b", "c"])

    def test_overlap_reported_without_merged_output(self):
        # 无冲突，但 current 新增的 cue 与 incoming 新增的 cue 时间重叠
        body = {
            "base": [],
            "current": [cue("a", 0, 5, "first")],
            "incoming": [cue("b", 3, 8, "second")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "overlap")
        self.assertNotIn("cues", data)
        self.assertEqual(len(data["overlaps"]), 1)
        ov = data["overlaps"][0]
        self.assertEqual(ov["cue_ids"], ["a", "b"])
        self.assertAlmostEqual(ov["start"], 3)
        self.assertAlmostEqual(ov["end"], 5)
        self.assertAlmostEqual(ov["overlap"], 2)

    def test_touching_intervals_are_not_overlap(self):
        # 首尾相接（end == start）不算重叠
        body = {
            "base": [],
            "current": [cue("a", 0, 5, "first")],
            "incoming": [cue("b", 5, 8, "second")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_conflict_blocks_overlap_check(self):
        # 同时存在冲突和潜在重叠：报告冲突，不进入重叠检测
        body = {
            "base": [cue("a", 0, 2, "x")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 0, 2, "two")],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["status"], "conflict")

    def test_invalid_interval_rejected(self):
        body = {
            "base": [cue("a", 0, 2)],
            "current": [cue("a", 5, 2)],
            "incoming": [cue("a", 0, 2)],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_duplicate_id_within_one_version_rejected(self):
        body = {
            "base": [cue("a", 0, 2), cue("a", 3, 4)],
            "current": [],
            "incoming": [],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_speaker_none_equivalence(self):
        # base 未提供 speaker 与一侧显式给出 speaker: null 视为未修改
        body = {
            "base": [{"id": "a", "start": 0, "end": 2, "text": "x"}],
            "current": [
                {"id": "a", "start": 0, "end": 2, "text": "x", "speaker": None}
            ],
            "incoming": [
                {"id": "a", "start": 0, "end": 2, "text": "x", "speaker": "bob"}
            ],
        }
        resp = client.post("/merge", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["cues"][0]["speaker"], "bob")


class ResolveTests(unittest.TestCase):
    # —— 无冲突：仅允许空决策 ——

    def test_no_conflict_empty_decisions_resolves(self):
        body = {
            "base": [cue("a", 0, 2, "hello")],
            "current": [cue("a", 0, 2, "hello!")],
            "incoming": [cue("a", 0, 2, "hello")],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["cues"][0]["text"], "hello!")

    def test_no_conflict_empty_lists_resolves(self):
        resp = client.post(
            "/resolve",
            json={
                "base": [cue("a", 0, 2)],
                "current": [cue("a", 0, 2)],
                "incoming": [cue("a", 0, 2)],
                "field_choices": [],
                "cue_choices": [],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "resolved")

    def test_no_conflict_with_field_choice_is_unknown(self):
        body = {
            "base": [cue("a", 0, 2)],
            "current": [cue("a", 0, 2)],
            "incoming": [cue("a", 0, 2)],
            "field_choices": [
                {"cue_id": "a", "field": "text", "choice": "current"}
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertEqual(data["status"], "decision_error")
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(data["errors"][0]["type"], "unknown")

    def test_no_conflict_with_cue_choice_is_unknown(self):
        body = {
            "base": [cue("a", 0, 2)],
            "current": [cue("a", 0, 2)],
            "incoming": [cue("a", 0, 2)],
            "cue_choices": [{"cue_id": "a", "choice": "delete"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"][0]["type"], "unknown")

    # —— 字段级冲突 ——

    def test_field_conflict_resolve_pick_side(self):
        for choice, expected in [
            ("base", "hi"),
            ("current", "one"),
            ("incoming", "two"),
        ]:
            with self.subTest(choice=choice):
                body = {
                    "base": [cue("a", 0, 2, "hi")],
                    "current": [cue("a", 0, 2, "one")],
                    "incoming": [cue("a", 0, 2, "two")],
                    "field_choices": [
                        {"cue_id": "a", "field": "text", "choice": choice}
                    ],
                }
                resp = client.post("/resolve", json=body)
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(resp.json()["cues"][0]["text"], expected)

    def test_field_choice_merges_non_conflicting_fields(self):
        # current 改文本，incoming 改文本（冲突）与起点；起点单侧改动仍自动采纳
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 1, 2, "two")],
            "field_choices": [
                {"cue_id": "a", "field": "text", "choice": "current"}
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        resolved = resp.json()["cues"][0]
        self.assertEqual(resolved["text"], "one")
        self.assertEqual(resolved["start"], 1)

    def test_multiple_field_conflicts_on_same_cue(self):
        body = {
            "base": [cue("a", 0, 10, "keep")],
            "current": [cue("a", 8, 9, "cur")],
            "incoming": [cue("a", 1, 3, "inc")],
            "field_choices": [
                {"cue_id": "a", "field": "start", "choice": "incoming"},
                {"cue_id": "a", "field": "end", "choice": "current"},
                {"cue_id": "a", "field": "text", "choice": "base"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        cue_out = resp.json()["cues"][0]
        self.assertEqual((cue_out["start"], cue_out["end"], cue_out["text"]), (1, 9, "keep"))

    def test_missing_field_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 0, 2, "two")],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["errors"]
        self.assertTrue(any(e["type"] == "missing" for e in errors))

    def test_missing_multiple_field_choices_all_reported(self):
        body = {
            "base": [cue("a", 0, 10, "keep")],
            "current": [cue("a", 8, 9, "cur")],
            "incoming": [cue("a", 1, 3, "inc")],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        missing = {e["field"] for e in resp.json()["errors"] if e["type"] == "missing"}
        self.assertEqual(missing, {"start", "end", "text"})

    def test_unknown_field_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 0, 2, "two")],
            "field_choices": [
                {"cue_id": "a", "field": "text", "choice": "current"},
                {"cue_id": "a", "field": "start", "choice": "current"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["errors"]
        unknown = [e for e in errors if e["type"] == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["field"], "start")

    def test_unknown_cue_id_in_field_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2)],
            "current": [cue("a", 0, 2)],
            "incoming": [cue("a", 0, 2)],
            "field_choices": [
                {"cue_id": "ghost", "field": "text", "choice": "base"}
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"][0]["type"], "unknown")

    def test_duplicate_field_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 0, 2, "two")],
            "field_choices": [
                {"cue_id": "a", "field": "text", "choice": "current"},
                {"cue_id": "a", "field": "text", "choice": "incoming"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        types = [e["type"] for e in resp.json()["errors"]]
        self.assertEqual(types, ["duplicate"])

    def test_field_choice_on_cue_conflict_is_type_mismatch(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "field_choices": [
                {"cue_id": "a", "field": "text", "choice": "incoming"}
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["errors"]
        self.assertTrue(any(e["type"] == "type_mismatch" for e in errors))
        self.assertTrue(any(e["type"] == "missing" and e["field"] is None for e in errors))

    # —— cue 级冲突 ——

    def test_delete_modify_pick_incoming(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [{"cue_id": "a", "choice": "incoming"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        cues = resp.json()["cues"]
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "two")

    def test_delete_modify_pick_deleted_side_deletes(self):
        # current 已删除，选择 current 等同删除
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [{"cue_id": "a", "choice": "current"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 0)

    def test_delete_modify_choice_delete(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [{"cue_id": "a", "choice": "delete"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["count"], 0)

    def test_add_add_pick_each_side(self):
        for choice, expected in [("current", "new"), ("incoming", "brand")]:
            with self.subTest(choice=choice):
                body = {
                    "base": [],
                    "current": [cue("n", 5, 7, "new")],
                    "incoming": [cue("n", 5, 7, "brand")],
                    "cue_choices": [{"cue_id": "n", "choice": choice}],
                }
                resp = client.post("/resolve", json=body)
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(resp.json()["cues"][0]["text"], expected)

    def test_invalid_interval_cue_conflict_pick_side(self):
        # current 只改起点、incoming 只改终点，自动合并为 [8,3] 非法
        body = {
            "base": [cue("a", 0, 10)],
            "current": [cue("a", 8, 10)],
            "incoming": [cue("a", 0, 3)],
        }
        conflict = client.post("/merge", json=body).json()
        self.assertEqual(conflict["cue_conflicts"][0]["type"], "invalid_interval")

        resolved_body = dict(body)
        resolved_body["cue_choices"] = [{"cue_id": "a", "choice": "current"}]
        resp = client.post("/resolve", json=resolved_body)
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["cues"][0]
        self.assertEqual((out["start"], out["end"]), (8, 10))

    def test_missing_cue_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["type"], "missing")
        self.assertEqual(errors[0]["cue_id"], "a")

    def test_unknown_cue_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2)],
            "current": [cue("a", 0, 2)],
            "incoming": [cue("a", 0, 2)],
            "cue_choices": [{"cue_id": "ghost", "choice": "incoming"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"][0]["type"], "unknown")

    def test_duplicate_cue_choice_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [
                {"cue_id": "a", "choice": "delete"},
                {"cue_id": "a", "choice": "incoming"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual([e["type"] for e in resp.json()["errors"]], ["duplicate"])

    def test_cue_choice_on_field_conflict_is_type_mismatch(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [cue("a", 0, 2, "one")],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [{"cue_id": "a", "choice": "delete"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)
        errors = resp.json()["errors"]
        self.assertTrue(any(e["type"] == "type_mismatch" and e["field"] is None for e in errors))

    def test_invalid_choice_value_is_422(self):
        body = {
            "base": [cue("a", 0, 2, "hi")],
            "current": [],
            "incoming": [cue("a", 0, 2, "two")],
            "cue_choices": [{"cue_id": "a", "choice": "base"}],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 422)

    # —— 应用决策后的统一校验（409，不输出部分结果）——

    def test_field_decisions_producing_invalid_interval_409(self):
        body = {
            "base": [cue("a", 0, 10, "keep")],
            "current": [cue("a", 8, 9, "cur")],
            "incoming": [cue("a", 1, 3, "inc")],
            "field_choices": [
                {"cue_id": "a", "field": "start", "choice": "current"},
                {"cue_id": "a", "field": "end", "choice": "incoming"},
                {"cue_id": "a", "field": "text", "choice": "base"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "timeline_error")
        self.assertEqual(data["invalid_intervals"], ["a"])
        self.assertEqual(data["overlaps"], [])
        self.assertNotIn("cues", data)

    def test_field_decisions_producing_overlap_409(self):
        # a、b 各自字段冲突；选 incoming 侧时间后 a=[1,4]、b=[3,12] 重叠
        body = {
            "base": [cue("a", 0, 10, "x"), cue("b", 10, 12, "y")],
            "current": [cue("a", 8, 9, "cx"), cue("b", 9, 12, "cy")],
            "incoming": [cue("a", 1, 4, "ix"), cue("b", 3, 12, "iy")],
            "field_choices": [
                {"cue_id": "a", "field": "start", "choice": "incoming"},
                {"cue_id": "a", "field": "end", "choice": "incoming"},
                {"cue_id": "a", "field": "text", "choice": "incoming"},
                {"cue_id": "b", "field": "start", "choice": "incoming"},
                {"cue_id": "b", "field": "text", "choice": "incoming"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "timeline_error")
        self.assertEqual(data["invalid_intervals"], [])
        self.assertEqual(len(data["overlaps"]), 1)
        self.assertEqual(data["overlaps"][0]["cue_ids"], ["a", "b"])

    def test_touching_intervals_after_decisions_resolve(self):
        # 同样的冲突，选 current 侧：a=[8,9]、b=[9,12]，首尾相接合法
        body = {
            "base": [cue("a", 0, 10, "x"), cue("b", 10, 12, "y")],
            "current": [cue("a", 8, 9, "cx"), cue("b", 9, 12, "cy")],
            "incoming": [cue("a", 1, 4, "ix"), cue("b", 3, 12, "iy")],
            "field_choices": [
                {"cue_id": "a", "field": "start", "choice": "current"},
                {"cue_id": "a", "field": "end", "choice": "current"},
                {"cue_id": "a", "field": "text", "choice": "current"},
                {"cue_id": "b", "field": "start", "choice": "current"},
                {"cue_id": "b", "field": "text", "choice": "current"},
            ],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [c["id"] for c in resp.json()["cues"]]
        self.assertEqual(ids, ["a", "b"])

    def test_no_conflict_but_overlap_still_409(self):
        body = {
            "base": [],
            "current": [cue("a", 0, 5, "first")],
            "incoming": [cue("b", 3, 8, "second")],
        }
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "timeline_error")
        self.assertEqual(len(data["overlaps"]), 1)

    def test_resolved_timeline_is_sorted_by_time(self):
        # cue 级冲突与干净合并同时存在，结果统一按时间排序
        body = {
            "base": [
                cue("a", 0, 2, "hi"),
                cue("d", 20, 21, "keep"),
            ],
            "current": [cue("d", 20, 21, "keep")],
            "incoming": [
                cue("a", 0, 2, "two"),
                cue("d", 20, 21, "keep"),
                cue("c", 10, 11, "late"),
            ],
            "cue_choices": [{"cue_id": "a", "choice": "incoming"}],
        }
        # 再让 current 单独新增一条更早的 cue
        body["current"].append(cue("b", 5, 6, "mid"))
        resp = client.post("/resolve", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [c["id"] for c in resp.json()["cues"]]
        self.assertEqual(ids, ["a", "b", "c", "d"])


class RetimeTests(unittest.TestCase):
    def test_linear_scale_within_single_segment(self):
        # anchors (0,0)-(10,20)：整体 2 倍速
        body = {
            "cues": [cue("a", 1, 3, "hello", speaker="bob")],
            "anchors": [anchor(0, 0), anchor(10, 20)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "retimed")
        self.assertEqual(data["count"], 1)
        out = data["cues"][0]
        self.assertAlmostEqual(out["start"], 2.0)
        self.assertAlmostEqual(out["end"], 6.0)
        # id / text / speaker 保持不变
        self.assertEqual((out["id"], out["text"], out["speaker"]), ("a", "hello", "bob"))

    def test_piecewise_interpolation_across_segments(self):
        # 0->0, 10->20, 20->25：区间内分段插值
        body = {
            "cues": [
                cue("a", 5, 15),   # 10（首段）, 22.5（次段）
                cue("b", 12, 18),  # 21, 24
            ],
            "anchors": [anchor(0, 0), anchor(10, 20), anchor(20, 25)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        cues = resp.json()["cues"]
        self.assertAlmostEqual(cues[0]["start"], 10.0)
        self.assertAlmostEqual(cues[0]["end"], 22.5)
        self.assertAlmostEqual(cues[1]["start"], 21.0)
        self.assertAlmostEqual(cues[1]["end"], 24.0)

    def test_exact_anchor_positions_map_to_target(self):
        body = {
            "cues": [cue("a", 0, 10), cue("b", 10, 20)],
            "anchors": [anchor(0, 0), anchor(10, 20), anchor(20, 25)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        cues = resp.json()["cues"]
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[0]["end"], 20.0)
        self.assertAlmostEqual(cues[1]["start"], 20.0)
        self.assertAlmostEqual(cues[1]["end"], 25.0)

    def test_extrapolation_beyond_outer_anchors(self):
        # 10->5, 20->15（斜率 1）：t=0 外推为 -5，t=30 外推为 25
        body = {
            "cues": [cue("far", 30, 31)],
            "anchors": [anchor(10, 5), anchor(20, 15)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["cues"][0]
        self.assertAlmostEqual(out["start"], 25.0)
        self.assertAlmostEqual(out["end"], 26.0)

    def test_cue_spanning_anchor_uses_independent_endpoint_mapping(self):
        # 一条 cue 跨越锚点：start 在首段、end 在次段，分别独立计算
        body = {
            "cues": [cue("span", 5, 15, "cross")],
            "anchors": [anchor(0, 0), anchor(10, 20), anchor(20, 25)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["cues"][0]
        self.assertAlmostEqual(out["start"], 10.0)
        self.assertAlmostEqual(out["end"], 22.5)

    def test_output_preserves_input_order(self):
        body = {
            "cues": [
                cue("late", 80, 81),
                cue("early", 1, 2),
                cue("mid", 40, 41),
            ],
            "anchors": [anchor(0, 0), anchor(100, 100)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            [c["id"] for c in resp.json()["cues"]], ["late", "early", "mid"]
        )

    def test_identity_anchors_leave_times_unchanged(self):
        body = {
            "cues": [cue("a", 0, 0.5), cue("b", 100, 100.25)],
            "anchors": [anchor(0, 0), anchor(100, 100)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        cues = resp.json()["cues"]
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[0]["end"], 0.5)
        self.assertAlmostEqual(cues[1]["start"], 100.0)
        self.assertAlmostEqual(cues[1]["end"], 100.25)

    def test_negative_mapped_time_returns_409_with_all_cue_ids(self):
        # 10->5, 20->15（斜率 1）：start=0/0.5 外推为 -5/-4.5；中间 cue 合法
        body = {
            "cues": [
                cue("bad-start", 0, 11),    # -5, 6（负）
                cue("ok", 12, 13),          #  7, 8（合法，但不输出部分结果）
                cue("bad-end", 0.5, 11),    # -4.5, 6（负）
            ],
            "anchors": [anchor(10, 5), anchor(20, 15)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["status"], "retime_error")
        self.assertNotIn("cues", data)
        # 列出全部相关 cue id，按输入顺序去重
        self.assertEqual(data["cue_ids"], ["bad-start", "bad-end"])

    def test_negative_extrapolation_at_first_anchor(self):
        # 首个锚点 source>0、target 较小：首段外推使早于锚点的时间变负
        body = {
            "cues": [cue("a", 4, 8)],  # -1, 3
            "anchors": [anchor(10, 5), anchor(20, 15)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["cue_ids"], ["a"])

    def test_float_collapsed_interval_is_409(self):
        # 锚点合法（target 严格递增 1 ULP），但极窄区间映射后两端四舍五入为
        # 同一浮点数 → end <= start，判为非法区间
        body = {
            "cues": [
                {"id": "tiny", "start": 5.000000000000001, "end": 5.000000000000002, "text": "x"}
            ],
            "anchors": [
                anchor(0, 1.0),
                anchor(10, 1.0000000000000002),
            ],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["cue_ids"], ["tiny"])

    def test_duplicate_ids_allowed_and_both_reported_once(self):
        # /retime 不做 id 去重；同一坏 id 只列一次
        body = {
            "cues": [
                cue("dup", 0, 11),
                cue("dup", 0, 11),
                cue("ok", 15, 16),
            ],
            "anchors": [anchor(10, 5), anchor(20, 15)],
        }
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["cue_ids"], ["dup"])

    def test_fewer_than_two_anchors_is_422(self):
        for anchors in ([], [anchor(0, 0)]):
            with self.subTest(n=len(anchors)):
                resp = client.post(
                    "/retime", json={"cues": [cue("a", 0, 1)], "anchors": anchors}
                )
                self.assertEqual(resp.status_code, 422)

    def test_non_increasing_source_anchors_is_422(self):
        # source 相等（非严格递增）
        body = {
            "cues": [cue("a", 0, 1)],
            "anchors": [anchor(0, 0), anchor(0, 10), anchor(10, 20)],
        }
        self.assertEqual(client.post("/retime", json=body).status_code, 422)
        # source 降序
        body["anchors"] = [anchor(10, 0), anchor(0, 10)]
        self.assertEqual(client.post("/retime", json=body).status_code, 422)

    def test_non_increasing_target_anchors_is_422(self):
        body = {
            "cues": [cue("a", 0, 1)],
            "anchors": [anchor(0, 10), anchor(10, 10), anchor(20, 20)],
        }
        self.assertEqual(client.post("/retime", json=body).status_code, 422)
        body["anchors"] = [anchor(0, 20), anchor(10, 10)]
        self.assertEqual(client.post("/retime", json=body).status_code, 422)

    def test_negative_anchor_values_is_422(self):
        body = {
            "cues": [cue("a", 0, 1)],
            "anchors": [anchor(-1, 0), anchor(10, 10)],
        }
        self.assertEqual(client.post("/retime", json=body).status_code, 422)
        body["anchors"] = [anchor(0, -1), anchor(10, 10)]
        self.assertEqual(client.post("/retime", json=body).status_code, 422)

    def test_empty_cues_retimes_to_empty_list(self):
        body = {"cues": [], "anchors": [anchor(0, 0), anchor(10, 10)]}
        resp = client.post("/retime", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "retimed")
        self.assertEqual(data["cues"], [])
        self.assertEqual(data["count"], 0)


class SpliceTests(unittest.TestCase):
    def test_empty_selections_returns_empty_and_zero_duration(self):
        body = {"cues": [cue("a", 0, 5, "hi", speaker="bob")], "selections": []}
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json(),
            {"status": "spliced", "cues": [], "count": 0, "duration": 0.0},
        )

    def test_omitted_fields_default_to_empty(self):
        resp = client.post("/splice", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json(),
            {"status": "spliced", "cues": [], "count": 0, "duration": 0.0},
        )

    def test_single_selection_clips_strict_intersection(self):
        # 源 cue 0-10 与选区 5-12 严格相交部分为 5-10，映射到 0-5；10-12 为空白
        body = {
            "cues": [cue("a", 0, 10, "hello", speaker="alice")],
            "selections": [selection(5, 12)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "spliced")
        self.assertAlmostEqual(data["duration"], 7.0)
        self.assertEqual(data["count"], 1)
        out = data["cues"][0]
        self.assertAlmostEqual(out["start"], 0.0)
        self.assertAlmostEqual(out["end"], 5.0)
        self.assertEqual(out["text"], "hello")
        self.assertEqual(out["speaker"], "alice")
        self.assertEqual(out["source_id"], "a")
        self.assertEqual(out["selection_index"], 0)
        self.assertEqual(out["id"], "a#0")

    def test_touching_boundaries_do_not_intersect_but_count_as_duration(self):
        # cue [0,5] 与选区 [5,8] 仅首尾相接：无片段输出，但选区时长 3s 计入
        body = {"cues": [cue("a", 0, 5)], "selections": [selection(5, 8)]}
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["cues"], [])
        self.assertAlmostEqual(data["duration"], 3.0)

    def test_cue_spanning_selection_both_edges_is_clipped(self):
        body = {"cues": [cue("a", 2, 8)], "selections": [selection(4, 6)]}
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["cues"][0]
        self.assertAlmostEqual(out["start"], 0.0)
        self.assertAlmostEqual(out["end"], 2.0)

    def test_offset_is_sum_of_previous_selection_durations(self):
        # 选区 [5,12)(7s)、[0,10)(10s)、[18,19)(1s)，总时长 18s
        # a 片段：选区内 5-10 -> 0-5；a 全段 -> 7-17；b[15,20] 交集 18-19 -> 17-18
        body = {
            "cues": [
                cue("a", 0, 10, "hello", speaker="alice"),
                cue("b", 15, 20, "world", speaker="bob"),
            ],
            "selections": [selection(5, 12), selection(0, 10), selection(18, 19)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertAlmostEqual(data["duration"], 18.0)
        got = [
            (c["id"], c["source_id"], c["selection_index"], c["start"], c["end"])
            for c in data["cues"]
        ]
        self.assertEqual(
            got,
            [
                ("a#0", "a", 0, 0.0, 5.0),
                ("a#1", "a", 1, 7.0, 17.0),
                ("b#2", "b", 2, 17.0, 18.0),
            ],
        )

    def test_inner_offset_relative_to_selection_start(self):
        # 选区内 cue 起点相对选区起点的偏移保留
        body = {"cues": [cue("a", 3, 4)], "selections": [selection(0, 10)]}
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        out = resp.json()["cues"][0]
        self.assertAlmostEqual(out["start"], 3.0)
        self.assertAlmostEqual(out["end"], 4.0)

    def test_gap_between_cues_inside_selection_is_blank(self):
        # 选区 0-10 内只有 [1,2) 与 [8,9) 两段字幕，中间空白计入时长
        body = {
            "cues": [cue("a", 1, 2), cue("b", 8, 9)],
            "selections": [selection(0, 10)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertAlmostEqual(data["duration"], 10.0)
        self.assertEqual(
            [(c["source_id"], c["start"], c["end"]) for c in data["cues"]],
            [("a", 1.0, 2.0), ("b", 8.0, 9.0)],
        )

    def test_unordered_overlapping_repeated_selections(self):
        # 乱序、重叠、重复：选区 [2,4)、[0,2)、[2,4)
        body = {
            "cues": [cue("a", 0, 4, "x")],
            "selections": [selection(2, 4), selection(0, 2), selection(2, 4)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertAlmostEqual(data["duration"], 6.0)
        got = sorted(
            ((c["id"], c["start"], c["end"], c["selection_index"]) for c in data["cues"]),
            key=lambda item: item[1],
        )
        self.assertEqual(
            got,
            [("a#0", 0.0, 2.0, 0), ("a#1", 2.0, 4.0, 1), ("a#2", 4.0, 6.0, 2)],
        )
        # 同一原 cue 多次入选：新 id 互不相同，但 source_id 相同
        ids = [c["id"] for c in data["cues"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(c["source_id"] == "a" for c in data["cues"]))

    def test_output_sorted_by_new_timeline(self):
        # 源 cue 输入乱序；同一选区内多个命中片段按新时间（即源起点）排序
        body = {
            "cues": [cue("b", 5, 6), cue("a", 0, 3), cue("c", 3, 4)],
            "selections": [selection(0, 10)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            [(c["source_id"], c["selection_index"]) for c in resp.json()["cues"]],
            [("a", 0), ("c", 0), ("b", 0)],
        )

    def test_selection_beyond_source_is_all_blank(self):
        body = {"cues": [cue("a", 0, 1)], "selections": [selection(100, 103.5)]}
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["cues"], [])
        self.assertAlmostEqual(data["duration"], 3.5)

    def test_text_and_speaker_preserved_including_null(self):
        body = {
            "cues": [
                {"id": "a", "start": 0, "end": 1, "text": "你好，世界"},
                cue("b", 1, 2, "yo", speaker="carol"),
            ],
            "selections": [selection(0, 2)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        by_source = {c["source_id"]: c for c in resp.json()["cues"]}
        self.assertEqual(by_source["a"]["text"], "你好，世界")
        self.assertIsNone(by_source["a"]["speaker"])
        self.assertEqual(by_source["b"]["speaker"], "carol")

    def test_duplicate_source_ids_is_param_error_without_partial_output(self):
        body = {
            "cues": [cue("a", 0, 5), cue("b", 3, 4), cue("a", 6, 7)],
            "selections": [selection(0, 10)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertEqual(data["status"], "splice_error")
        self.assertEqual(data["duplicated_ids"], ["a"])
        self.assertNotIn("cues", data)

    def test_overlapping_source_timeline_is_param_error(self):
        body = {
            "cues": [cue("a", 0, 5, "first"), cue("b", 3, 8, "second")],
            "selections": [selection(0, 10)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 422)
        data = resp.json()
        self.assertEqual(data["status"], "splice_error")
        self.assertEqual(data["duplicated_ids"], [])
        self.assertEqual(len(data["overlaps"]), 1)
        ov = data["overlaps"][0]
        self.assertEqual(ov["cue_ids"], ["a", "b"])
        self.assertAlmostEqual(ov["start"], 3.0)
        self.assertAlmostEqual(ov["end"], 5.0)
        self.assertAlmostEqual(ov["overlap"], 2.0)
        self.assertNotIn("cues", data)

    def test_touching_source_intervals_are_not_an_error(self):
        # 源时间轴首尾相接合法，可以正常剪取
        body = {
            "cues": [cue("a", 0, 5), cue("b", 5, 8)],
            "selections": [selection(0, 8)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([c["source_id"] for c in resp.json()["cues"]], ["a", "b"])

    def test_invalid_selection_endpoints_are_422(self):
        for bad in (selection(-1, 2), selection(2, 2), selection(3, 2.5)):
            with self.subTest(bad=bad):
                resp = client.post(
                    "/splice", json={"cues": [cue("a", 0, 5)], "selections": [bad]}
                )
                self.assertEqual(resp.status_code, 422)

    def test_invalid_source_cue_is_422(self):
        # 源 cue 本身非法（end <= start）属于请求体校验失败
        body = {"cues": [cue("a", 5, 2)], "selections": [selection(0, 1)]}
        self.assertEqual(client.post("/splice", json=body).status_code, 422)

    def test_zero_length_output_cues_are_never_emitted(self):
        # cue 完全在选区之外；选区内起点恰好等于 cue 终点也不产生片段
        body = {
            "cues": [cue("a", 0, 2), cue("b", 20, 21)],
            "selections": [selection(2, 20)],
        }
        resp = client.post("/splice", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["cues"], [])
        self.assertAlmostEqual(data["duration"], 18.0)
        self.assertTrue(all(c["end"] > c["start"] for c in data["cues"]))


if __name__ == "__main__":
    unittest.main()
