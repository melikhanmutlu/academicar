"""static/js/mp4-defragment.js turns a browser-recorded (fragmented) MP4 into
a plain MP4 so desktop players show its length and can seek. The fixture is
a real MediaRecorder recording from Chromium (moov+mvex, moof/mdat, mfra)."""
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "js" / "mp4-defragment.js"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chrome_fragmented.mp4"

RUNNER = r"""
globalThis.window = globalThis;
require(process.argv[1]);
const fs = require('fs');
const input = fs.readFileSync(process.argv[2]);
window.academicarDefragmentMp4(new Blob([input])).then(async (blob) => {
  fs.writeFileSync(process.argv[3], Buffer.from(await blob.arrayBuffer()));
});
"""

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _run(tmp_path, data):
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.bin"
    src.write_bytes(data)
    subprocess.run(["node", "-e", RUNNER, str(SCRIPT), str(src), str(dst)], check=True, timeout=30)
    return dst.read_bytes()


def _boxes(buf, start, end):
    off = start
    while off + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[off:off + 8])
        yield typ.decode("latin1"), off, size
        off += size


def _child(buf, box, name):
    _, off, size = box
    return next(b for b in _boxes(buf, off + 8, off + size) if b[0] == name)


def test_fragmented_mp4_becomes_plain_mp4_with_duration(tmp_path):
    original = FIXTURE.read_bytes()
    assert [b[0] for b in _boxes(original, 0, len(original))] == ["ftyp", "moov", "moof", "mdat", "mfra"]
    out = _run(tmp_path, original)
    top = list(_boxes(out, 0, len(out)))
    assert [b[0] for b in top] == ["ftyp", "moov", "mdat"]
    moov = top[1]
    assert "mvex" not in [b[0] for b in _boxes(out, moov[1] + 8, moov[1] + moov[2])]
    trak = _child(out, moov, "trak")
    stbl = _child(out, _child(out, _child(out, trak, "mdia"), "minf"), "stbl")
    stsz = _child(out, stbl, "stsz")
    sample_count = struct.unpack(">I", out[stsz[1] + 16:stsz[1] + 20])[0]
    assert sample_count == 3  # the fixture's trun holds three samples
    mdhd = _child(out, _child(out, trak, "mdia"), "mdhd")
    version = out[mdhd[1] + 8]
    timescale, duration = (
        struct.unpack(">IQ", out[mdhd[1] + 28:mdhd[1] + 40]) if version
        else struct.unpack(">II", out[mdhd[1] + 20:mdhd[1] + 28])
    )
    assert abs(duration / timescale - 2.543) < 0.01
    # Sample bytes are copied unchanged: the new mdat payload is the old one.
    old_mdat = next(b for b in _boxes(original, 0, len(original)) if b[0] == "mdat")
    new_mdat = top[2]
    assert out[new_mdat[1] + 8:new_mdat[1] + new_mdat[2]] == original[old_mdat[1] + 8:old_mdat[1] + old_mdat[2]]


def test_unexpected_input_is_returned_unchanged(tmp_path):
    junk = b"not a video at all" * 10
    assert _run(tmp_path, junk) == junk
    plain = _run(tmp_path, FIXTURE.read_bytes())
    assert _run(tmp_path, plain) == plain  # already plain: left alone
