"""Run every test suite against a freshly started bot. Exit code 0 only if all pass.

    python tests/run_all.py            # full run
    python tests/run_all.py --quick    # fewer soak / fuzz seeds
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
QUICK = "--quick" in sys.argv


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
URL = f"http://127.0.0.1:{PORT}"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def run(name, args):
    t = time.time()
    p = subprocess.run([sys.executable, *args], cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8")
    ok = p.returncode == 0
    last = [line for line in p.stdout.strip().splitlines() if "RESULT" in line or "ALL TESTS" in line]
    print(f"{'PASS' if ok else 'FAIL'}  {name:34} {time.time() - t:6.1f}s  {last[-1].strip() if last else ''}")
    if not ok:
        print("\n".join("      " + x for x in (p.stdout + p.stderr).strip().splitlines()[-15:]))
    return ok


def teardown():
    urllib.request.urlopen(urllib.request.Request(URL + "/v1/teardown", data=b"{}", method="POST"), timeout=10).read()


results = []
print("── offline ──")
for name, script in [("spec (challenge-brief §7)", "spec_test.py"), ("intents: tuning set", "test_reply_intents.py"),
                     ("intents: held-out #1", "held_out_intents.py"), ("intents: blind held-out #2", "held_out_intents_2.py"),
                     ("conversation quality (replay)", "conversation_test.py")]:
    results.append(run(name, [str(TESTS / script)]))

print(f"── live bot on {URL} ──")
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "bot:app", "--port", str(PORT), "--log-level", "warning"],
                          cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8")
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(URL + "/v1/healthz", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    results.append(run("e2e judge lifecycle (48 checks)", [str(TESTS / "e2e_judge_test.py"), URL]))
    teardown()
    for seed in (range(1, 4) if QUICK else range(1, 11)):
        results.append(run(f"soak simulated judge (seed {seed})", [str(TESTS / "soak_test.py"), URL, str(seed)]))
    for seed in ((1,) if QUICK else (1, 2, 3)):
        results.append(run(f"fuzz 3000 payloads (seed {seed})", [str(TESTS / "fuzz_test.py"), URL, "3000", str(seed)]))
        teardown()
    results.append(run("smoke replay scenarios", [str(TESTS / "test_bot.py"), URL]))
finally:
    server.terminate()
    try:
        _, err = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        err = ""
tracebacks = (err or "").count("Traceback")
print(f"{'PASS' if tracebacks == 0 else 'WARN'}  {'server tracebacks during run':34}         {tracebacks}")

results.append(run("restart + teardown resilience", [str(TESTS / "restart_test.py")]))
print(f"\n{sum(results)}/{len(results)} suites passed")
sys.exit(0 if all(results) else 1)
