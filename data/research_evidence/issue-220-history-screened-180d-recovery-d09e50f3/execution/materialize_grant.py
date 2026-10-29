import hashlib
import json
import sys
from pathlib import Path

from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    HistoryScreenedExecutionGrant,
)

if len(sys.argv) != 3:
    raise SystemExit("usage: materialize_grant.py OLD_GRANT NEW_GRANT")
old_path = Path(sys.argv[1]).resolve(strict=True)
new_path = Path(sys.argv[2]).resolve()
if old_path == new_path:
    raise SystemExit("original grant must remain unchanged")

expected = {
    "kind": "EXTERNAL_REVIEWED_EXECUTION_GRANT",
    "run_uuid": "ec10ca5d-01e6-436f-8153-fe9c5b5439b5",
    "executable_sha": "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e",
    "protocol_sha256": "817338014f02d1b9f048e382b9b861114160053b83a1062068442378f8c7e671",
    "task_plan_sha256": "1506b2568f6067d39ccebe3a4093349116cfcc92413dcbb2d961fdad52ecdd49",
    "source_id": "weex-history-screened-196d-last-continuation-v1",
    "dataset_version": "weex-history-screened-196d-last-continuation-v1",
    "manifest_sha256": "756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d",
}
old_bytes = old_path.read_bytes()
if (
    len(old_bytes) != 541
    or hashlib.sha256(old_bytes).hexdigest()
    != "13b8026d682b349ea37413d7addf2820199718034a2a4251badbbb5bed4fb300"
    or json.loads(old_bytes) != expected
):
    raise SystemExit("preserved failed grant does not match reviewed evidence")

payload = json.dumps(
    expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("utf-8")
expected_hash = "f674cb1dffdd87801f51f2eba27ecebc8130497ea0c92f5ae8a0526659f4963d"
if len(payload) != 540 or hashlib.sha256(payload).hexdigest() != expected_hash:
    raise SystemExit("canonical payload identity mismatch")
with new_path.open("xb") as handle:
    handle.write(payload)
if new_path.read_bytes() != payload:
    raise SystemExit("canonical grant read-back mismatch")
loaded = HistoryScreenedExecutionGrant.from_file(new_path)
loaded.validate(actual_sha=expected["executable_sha"])
if old_path.read_bytes() != old_bytes:
    raise SystemExit("original grant unexpectedly changed")
print("GRANT_LOADER_OK", len(payload), expected_hash)
