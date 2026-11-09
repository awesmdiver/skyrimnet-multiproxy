"""Benchmarks on another PC, step 1: the plan checks, the path rules, the filename check, the job state table and the capability check (no route, no process, no network)."""
import copy
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

import benchmarks_remote_plan as rp
from benchmarks_remote_plan import PlanRefused

SHA = "a" * 64
OWNER = "main-pc-0123456789"
CATALOGUE = {"qwen3-4b": {"file": "Qwen3-4B-Q4_K_M.gguf", "sha256": "b" * 64, "size": 2_500_000_000}, "plain": {"file": "plain.gguf"}}


def plan(**over):
    body = {"protocol_version": 1, "request_id": "req-0123456789", "owner_id": OWNER, "models": [{"id": "qwen3-4b"}]}
    body.update(over)
    return body


def refused(body, code, field=None, catalogue=CATALOGUE):
    with pytest.raises(PlanRefused) as e:
        rp.validate_plan(body, catalogue)
    assert e.value.code == code, (e.value.code, e.value.field, e.value.why)
    if field is not None:
        assert e.value.field == field
    return e.value


# --- accepted shapes ----------------------------------------------------------------------------------------------------------------------------------------------------------

def test_minimal_plan_gets_every_default_and_a_hash():
    p = rp.validate_plan(plan(), CATALOGUE)
    assert p["levels"] == [1, 4, 8] and p["requests_per_level"] == 25 and p["warmup"] == 2 and p["sustained"] is True
    assert p["while_testing"] == "start_normal" and p["keep_models"] is False and p["take_over"] is False
    assert p["models"] == [{"id": "qwen3-4b", "file": "Qwen3-4B-Q4_K_M.gguf", "sha256": "b" * 64, "size": 2_500_000_000, "source": "catalogue"}]
    assert len(p["plan_hash"]) == 64


def test_every_allowed_option_is_accepted():
    body = plan(levels=[16, 1, 2, 4, 8], requests_per_level=100, prompts=["long", "short"], temperature=1.5, sustained=False, sustained_s=1800, request_timeout_s=600, warmup=5, cooldown=False,
                cooldown_c=90, cooldown_max_s=900, ceiling_c=120, heat_stop=False, while_testing="stop", keep_started=True, keep_models=True, take_over=True,
                settings={"layers": "all", "flashAttn": "on", "cacheType": "q8_0", "ctx": 8192, "batch": 512, "threads": 8, "gpu": "GPU-1:RTX 4090"})
    p = rp.validate_plan(body, CATALOGUE)
    assert p["levels"] == [1, 2, 4, 8, 16] and p["prompts"] == ["short", "long"] and p["settings"]["gpu"] == "GPU-1:RTX 4090" and p["ceiling_c"] == 120
    assert rp.validate_plan(plan(settings={"layers": 33}), CATALOGUE)["settings"] == {"layers": 33}
    assert rp.validate_plan(plan(settings=None), CATALOGUE)["settings"] == {}


def test_a_model_sent_by_its_bytes_carries_its_own_file_hash_and_size():
    mid = f"sha256:{SHA}:1234567"
    p = rp.validate_plan(plan(models=[{"id": mid, "file": "My Model (v2).gguf", "sha256": SHA, "size": 1234567}]), {})
    assert p["models"][0]["source"] == "content" and p["models"][0]["file"] == "My Model (v2).gguf"


def test_the_hash_is_stable_and_ignores_the_request_id_but_not_the_plan():
    a = rp.validate_plan(plan(), CATALOGUE)
    b = rp.validate_plan(plan(request_id="another-req-id"), CATALOGUE)
    c = rp.validate_plan(plan(levels=[1]), CATALOGUE)
    assert a["plan_hash"] == b["plan_hash"] != c["plan_hash"]
    assert rp.validate_plan(plan(levels=[8, 4, 1, 4]), CATALOGUE)["plan_hash"] == a["plan_hash"]      # order and repeats don't matter


def test_the_input_is_not_changed():
    body = plan()
    before = copy.deepcopy(body)
    rp.validate_plan(body, CATALOGUE)
    assert body == before


# --- refusals -----------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_unknown_keys_are_refused_at_every_level():
    refused(plan(shell="calc"), "unknown_key", "shell")
    refused(plan(settings={"extraArgs": "--host 0.0.0.0"}), "unknown_key", "settings.extraArgs")
    refused(plan(models=[{"id": "qwen3-4b", "path": "C:\\x.gguf"}]), "unknown_key", "models[0].path")
    refused(plan(skyrim="closed"), "unknown_key", "skyrim")         # no Skyrim term on another device
    refused(plan(pair_of="run-1"), "unknown_key", "pair_of")


def test_required_keys_and_types():
    for k in ("protocol_version", "request_id", "owner_id", "models"):
        body = plan()
        del body[k]
        refused(body, "missing_key", k)
    refused([], "bad_type")
    refused(plan(models="qwen3-4b"), "bad_type", "models")
    refused(plan(models=["qwen3-4b"]), "bad_type", "models[0]")
    refused(plan(models=[{}]), "missing_key", "models[0].id")
    refused(plan(protocol_version=2), "bad_protocol")
    refused(plan(protocol_version=True), "bad_protocol")
    refused(plan(kind="voice"), "bad_value", "kind")


def test_a_url_where_an_id_belongs():
    refused(plan(models=[{"id": "https://huggingface.co/x/y/resolve/main/m.gguf"}]), "url_not_allowed", "models[0].id")
    refused(plan(models=[{"id": "qwen3-4b", "file": "http://evil.example/m.gguf"}]), "url_not_allowed", "models[0].file")
    refused(plan(models=[{"id": "ftp://host/m"}]), "url_not_allowed")
    refused(plan(request_id="www.evil.example/aaaa"), "url_not_allowed", "request_id")
    refused(plan(settings={"gpu": "https://x.example"}), "url_not_allowed", "settings.gpu")


def test_an_executable_or_output_path_where_an_id_belongs():
    for bad in ("C:\\Windows\\System32\\cmd.exe", "C:/models/x.gguf", "..\\..\\x.gguf", "../x.gguf", "/etc/passwd", "\\\\server\\share\\m.gguf", "~/m.gguf", "models/x.gguf"):
        refused(plan(models=[{"id": bad}]), "path_not_allowed", "models[0].id")
    refused(plan(models=[{"id": "qwen3-4b", "file": "..\\evil.gguf"}]), "path_not_allowed", "models[0].file")
    refused(plan(settings={"gpu": "C:\\out"}), "path_not_allowed", "settings.gpu")
    refused(plan(owner_id="../../owner-id"), "path_not_allowed", "owner_id")


def test_shell_characters_never_pass():
    for bad in ("a;b", "a&b", "a|b", "a`b`", "$(whoami)", "a b>c", 'a"b', "a'b", "a%PATH%", "a^b", "a\nb"):
        with pytest.raises(PlanRefused) as e:
            rp.validate_plan(plan(settings={"gpu": bad}), CATALOGUE)
        with pytest.raises(PlanRefused):
            rp.validate_plan(plan(models=[{"id": bad}]), CATALOGUE)
        with pytest.raises(PlanRefused):
            rp.validate_plan(plan(request_id=bad + "12345678"), CATALOGUE)
    refused(plan(models=[{"id": "qwen3-4b", "file": "a;calc.gguf"}]), "bad_value", None)


def test_a_model_the_catalogue_does_not_know_or_that_disagrees():
    refused(plan(models=[{"id": "unknown-model"}]), "unknown_model", "models[0].id")
    refused(plan(models=[{"id": "qwen3-4b", "file": "Other.gguf"}]), "model_mismatch", "models[0].file")
    refused(plan(models=[{"id": "qwen3-4b", "sha256": "c" * 64}]), "model_mismatch", "models[0].sha256")
    refused(plan(models=[{"id": "qwen3-4b", "size": 5}]), "model_mismatch", "models[0].size")
    refused(plan(models=[{"id": "plain", "sha256": "nothex"}]), "bad_value", "models[0].sha256")
    mid = f"sha256:{SHA}:100"
    refused(plan(models=[{"id": mid}]), "missing_key", "models[0].file")
    refused(plan(models=[{"id": mid, "file": "m.gguf", "sha256": "d" * 64, "size": 100}]), "model_mismatch", "models[0].sha256")
    refused(plan(models=[{"id": mid, "file": "m.gguf", "sha256": SHA, "size": 101}]), "model_mismatch", "models[0].size")
    refused(plan(models=[{"id": mid, "file": "m.exe", "sha256": SHA, "size": 100}]), "bad_value", "models[0].file")
    refused(plan(models=[{"id": mid, "file": "CON.gguf", "sha256": SHA, "size": 100}]), "bad_value", "models[0].file")


def test_model_counts_and_duplicates():
    refused(plan(models=[]), "out_of_range", "models")
    refused(plan(models=[{"id": "plain"}] * 2), "bad_value", "models")
    many = [{"id": f"sha256:{SHA[:-2]}{i:02x}:{100 + i}", "file": f"m{i}.gguf", "sha256": SHA[:-2] + f"{i:02x}", "size": 100 + i} for i in range(rp.MAX_MODELS + 1)]
    refused(plan(models=many), "out_of_range", "models")
    refused(plan(models=[{"id": f"sha256:{SHA}:100", "file": "m.gguf", "sha256": SHA, "size": 100}, {"id": f"sha256:{'e' * 64}:100", "file": "M.gguf", "sha256": "e" * 64, "size": 100}]), "bad_value", "models")


def test_huge_and_out_of_range_values():
    refused(plan(requests_per_level=101), "out_of_range", "requests_per_level")
    refused(plan(requests_per_level=0), "out_of_range")
    refused(plan(requests_per_level=10 ** 18), "out_of_range")
    refused(plan(sustained_s=1801), "out_of_range", "sustained_s")
    refused(plan(request_timeout_s=601), "out_of_range")
    refused(plan(temperature=2.1), "out_of_range")
    refused(plan(warmup=6), "out_of_range")
    refused(plan(cooldown_c=29), "out_of_range")
    refused(plan(ceiling_c=121), "out_of_range")
    refused(plan(settings={"ctx": 10 ** 9}), "out_of_range", "settings.ctx")
    refused(plan(settings={"batch": 31}), "out_of_range")
    refused(plan(settings={"threads": 0}), "out_of_range")
    refused(plan(settings={"layers": 1000}), "out_of_range")
    refused(plan(temperature=float("nan")), "bad_value")
    refused(plan(temperature=float("inf")), "bad_value")
    refused(plan(models=[{"id": f"sha256:{SHA}:{rp.MAX_MODEL_BYTES + 1}", "file": "m.gguf", "sha256": SHA, "size": rp.MAX_MODEL_BYTES + 1}]), "out_of_range")
    refused(plan(request_id="x" * 5000), "too_big")
    refused(plan(models=[{"id": "plain"}], settings={"gpu": "G" * 301}), "too_big")


def test_levels_come_from_the_allowed_set():
    refused(plan(levels=[3]), "out_of_range", "levels[0]")
    refused(plan(levels=[32]), "out_of_range")
    refused(plan(levels=[]), "out_of_range", "levels")
    refused(plan(levels=[1, 2, 4, 8, 16, 1]), "out_of_range", "levels")
    refused(plan(levels=[True]), "out_of_range")
    refused(plan(levels="1"), "out_of_range", "levels")
    refused(plan(prompts=["huge"]), "bad_value", "prompts")
    refused(plan(prompts=[]), "bad_value")


def test_the_while_testing_choice_and_flags_are_checked():
    refused(plan(while_testing="kill"), "bad_value", "while_testing")
    refused(plan(keep_models="yes"), "bad_type", "keep_models")
    refused(plan(take_over=1), "bad_type", "take_over")
    refused(plan(settings={"flashAttn": "maybe"}), "bad_value")
    refused(plan(settings={"cacheType": "q2_k"}), "bad_value")
    refused(plan(settings={"layers": "most"}), "bad_value")
    refused(plan(settings="all"), "bad_type", "settings")


def test_nesting_and_odd_values():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    refused(plan(settings=deep), "too_big")
    refused(plan(settings={"gpu": "ok", "x\x00": 1}), "bad_type")
    refused(plan(request_id="req-\x00123456789"), "bad_value")
    refused({"protocol_version": 1, 5: "x"}, "bad_type")


# --- paths --------------------------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def work():
    base = Path(tempfile.gettempdir()) / "mp-benchmarks-remote-step1"
    d = base / uuid.uuid4().hex[:10]
    (d / "models" / rp.JOBS_SUBDIR / "job-12345678").mkdir(parents=True)
    (d / "outside").mkdir()
    (d / "outside" / "secret.txt").write_text("secret", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_a_job_file_sits_inside_the_job_folder(work):
    p = rp.job_file(work / "models", "job-12345678", "m.gguf")
    assert p == Path(os.path.realpath(work / "models")) / rp.JOBS_SUBDIR / "job-12345678" / "m.gguf"


@pytest.mark.parametrize("rel", ["", "..", "../x", "a/../../x", "/abs", "C:/x", "C:x", "a\\b", "a//b", "a/./b", "a/", "NUL", "dir/COM1.txt", "aux.gguf", "a/b.", "a/b ", "a:stream", "a/b?c", "a*", "x<y", "x|y", 'x"y', "a/\x01", "a/COM\u00b9",
                                 "\\\\server\\share", "x" * 500])
def test_containment_refuses(work, rel):
    with pytest.raises(ValueError):
        rp.contain(work / "models", rel)


def test_containment_refuses_what_is_not_text(work):
    for rel in (None, 5, b"x", ["a"]):
        with pytest.raises(ValueError):
            rp.contain(work / "models", rel)


def test_job_file_re_checks_both_pieces(work):
    for job, name in (("..", "m.gguf"), ("job/../x", "m.gguf"), ("job-12345678", "..\\m.gguf"), ("job-12345678", "m.exe"), ("job-12345678", "sub/m.gguf"), ("short", "m.gguf"), ("job-12345678", "CON.gguf"), ("job-12345678", "")):
        with pytest.raises(ValueError):
            rp.job_file(work / "models", job, name)


def test_a_stand_in_link_in_the_path_is_refused(work):
    models = work / "models"
    linked = str(Path(os.path.realpath(models)) / rp.JOBS_SUBDIR / "job-12345678")
    with pytest.raises(ValueError, match="link"):
        rp.contain(models, f"{rp.JOBS_SUBDIR}/job-12345678/m.gguf", is_link=lambda p: p == linked)
    rp.contain(models, f"{rp.JOBS_SUBDIR}/job-12345678/m.gguf", is_link=lambda p: False)


@pytest.mark.skipif(os.name != "nt", reason="a directory junction is a Windows thing")
def test_a_real_junction_out_of_the_job_folder_is_refused(work):
    job = work / "models" / rp.JOBS_SUBDIR / "job-12345678"
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(job / "escape.gguf"), str(work / "outside")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    try:
        with pytest.raises(ValueError, match="link"):
            rp.contain(work / "models", f"{rp.JOBS_SUBDIR}/job-12345678/escape.gguf/secret.txt")
        with pytest.raises(ValueError, match="link"):
            rp.job_file(work / "models", "job-12345678", "escape.gguf")
        rp.job_file(work / "models", "job-12345678", "fine.gguf")               # the same folder without the junction is fine
    finally:
        os.rmdir(job / "escape.gguf")                                                  # (removes the junction, not what it points to)
    assert (work / "outside" / "secret.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="the job folder itself as a junction is a Windows thing")
def test_the_job_folder_itself_being_a_junction_is_refused(work):
    jobs = work / "models" / rp.JOBS_SUBDIR
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(jobs / "job-evil-0001"), str(work / "outside")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    try:
        with pytest.raises(ValueError, match="link"):
            rp.job_file(work / "models", "job-evil-0001", "m.gguf")
    finally:
        os.rmdir(jobs / "job-evil-0001")


def test_a_real_symlink_is_refused_where_the_system_allows_one(work):
    job = work / "models" / rp.JOBS_SUBDIR / "job-12345678"
    try:
        os.symlink(work / "outside", job / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")
    with pytest.raises(ValueError, match="link"):
        rp.contain(work / "models", f"{rp.JOBS_SUBDIR}/job-12345678/link/secret.txt")


def test_the_root_itself_is_not_a_valid_target(work):
    with pytest.raises(ValueError):
        rp.contain(work / "models", "a/..")


def test_result_file_destination(work):
    runs = work / "models"
    assert rp.result_file(runs, "run-2026-10-28_abc", "manifest.json").name == "manifest.json"
    for run, name in (("..", "manifest.json"), ("run/../x", "manifest.json"), ("run-1", "evil.exe"), ("run-1", "../manifest.json"), ("run-1", "manifest.json "), ("", "manifest.json"), ("run 1", "summary.json")):
        with pytest.raises(ValueError):
            rp.result_file(runs, run, name)


# --- filenames ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_only_the_five_result_files_are_valid_names():
    for n in rp.RESULT_FILES:
        assert rp.valid_result_filename(n)
    for n in ("../manifest.json", "manifest.json.exe", "Manifest.json", "manifest.json ", "manifest.JSON", "engine.log", "", None, 5, "requests.jsonl\x00.exe", "a/manifest.json", "CON", "manifest.json:stream"):
        assert not rp.valid_result_filename(n), n


def test_safe_component_rules():
    for ok in ("model.gguf", "run-2026_10", "a b", "x" * 200):
        assert rp.safe_component(ok), ok
    for bad in ("", ".", "..", "a.", "a ", " a", "con", "Nul.txt", "COM3", "lpt9.log", "a/b", "a\\b", "a:b", "a*b", "a?b", "a\x00", "x" * 201, None, 3):
        assert not rp.safe_component(bad), bad


# --- the job states -----------------------------------------------------------------------------------------------------------------------------------------------------------

def test_every_listed_move_is_legal_for_its_actor_and_for_nobody_else():
    for old, moves in rp._JOB_MOVES.items():
        for new, actor in moves.items():
            other = rp.ACTOR_OWNER if actor == rp.ACTOR_COURIER else rp.ACTOR_COURIER
            assert rp.can_move_job(old, new, actor)
            assert not rp.can_move_job(old, new, other)
            assert rp.move_job(old, new, actor, reason="why") == new


def test_illegal_moves_refuse():
    legal = {(o, n) for o, m in rp._JOB_MOVES.items() for n in m}
    for old in rp.JOB_STATES:
        for new in rp.JOB_STATES:
            if (old, new) in legal:
                continue
            for actor in (rp.ACTOR_COURIER, rp.ACTOR_OWNER):
                assert not rp.can_move_job(old, new, actor)
                with pytest.raises(ValueError):
                    rp.move_job(old, new, actor, reason="x")


def test_the_named_rules_of_the_table():
    assert not rp._JOB_MOVES.get(rp.J_COLLECTED)                                  # collected never moves
    for ended in rp.ENDED_STATES:
        assert set(rp._JOB_MOVES[ended]) == {rp.J_COLLECTED}                      # an ended job only waits for the acknowledge
    for active in rp.ACTIVE_STATES:
        assert rp.J_COLLECTED not in rp._JOB_MOVES[active]                        # nothing is collected before it has ended
    with pytest.raises(ValueError):
        rp.move_job(rp.J_RUNNING, rp.J_FINISHED, rp.ACTOR_OWNER)                  # the main PC cannot declare a job finished
    with pytest.raises(ValueError):
        rp.move_job(rp.J_RUNNING, rp.J_COLLECTED, rp.ACTOR_OWNER)                 # nor collect one that still runs
    assert rp.move_job(rp.J_FINISHED, rp.J_COLLECTED, rp.ACTOR_OWNER) == rp.J_COLLECTED
    assert rp.move_job(rp.J_RUNNING, rp.J_FINISHED, rp.ACTOR_COURIER) == rp.J_FINISHED


def test_a_failed_interrupted_or_cancelled_job_needs_a_reason():
    for new in rp.REASON_STATES:
        with pytest.raises(ValueError, match="reason"):
            rp.move_job(rp.J_RUNNING, new, rp.ACTOR_COURIER)
        assert rp.move_job(rp.J_RUNNING, new, rp.ACTOR_COURIER, reason="lost_contact") == new
    with pytest.raises(ValueError):
        rp.move_job("running", "bogus", rp.ACTOR_COURIER)
    with pytest.raises(ValueError):
        rp.move_job("bogus", "running", rp.ACTOR_COURIER)


# --- compatibility ------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_missing_capabilities():
    p = rp.validate_plan(plan(), CATALOGUE)
    need = rp.required_capabilities(p)
    assert rp.CAP_START_SERVICES in need and rp.CAP_RECEIVE_MODELS not in need
    assert rp.CAP_RECEIVE_MODELS in rp.required_capabilities(p, models_missing=True)
    assert rp.CAP_START_SERVICES not in rp.required_capabilities(rp.validate_plan(plan(while_testing="stop"), CATALOGUE))
    full = {"protocol_version": 1, "capabilities": list(need)}
    assert rp.missing_capabilities(full, need) == []
    assert rp.missing_capabilities({"protocol_version": 1, "capabilities": [c for c in need if c != rp.CAP_ACK]}, need) == [rp.CAP_ACK]
    assert rp.missing_capabilities({"protocol_version": 2, "capabilities": list(need)}, need) == list(need)      # another protocol: nothing counts
    for report in (None, {}, "x", {"protocol_version": 1}, {"protocol_version": 1, "capabilities": "all"}, {"protocol_version": 1, "capabilities": [1, 2]}):
        assert rp.missing_capabilities(report, need) == list(need)
