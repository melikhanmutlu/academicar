/*
 * AcademicAR — turn a browser-recorded (fragmented) MP4 into a plain MP4.
 *
 * MediaRecorder writes MP4 as fragments (moov + mvex, then moof/mdat pairs).
 * Many desktop players (QuickTime, Windows Media Player, PowerPoint) cannot
 * show the length of such a file or seek in it. This rewrites the container
 * only — no re-encoding: the samples are copied as-is into one mdat and the
 * moov gets ordinary sample tables (stts/stsz/stsc/stco/stss/ctts) and real
 * durations. Anything unexpected (several tracks, odd offsets) returns the
 * original blob unchanged.
 *
 * Usage: blob = await window.academicarDefragmentMp4(blob)
 */
(function () {
  'use strict';

  var CONTAINERS = { moov: 1, trak: 1, mdia: 1, minf: 1, stbl: 1, mvex: 1, moof: 1, traf: 1, edts: 1, dinf: 1 };

  function u32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
  function i32(b, o) { return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]; }
  function u64(b, o) { return u32(b, o) * 4294967296 + u32(b, o + 4); }
  function u24(b, o) { return (b[o] << 16) + (b[o + 1] << 8) + b[o + 2]; }
  function fourcc(b, o) { return String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]); }

  function parse(b, start, end) {
    var out = [];
    var o = start;
    while (o + 8 <= end) {
      var size = u32(b, o);
      var type = fourcc(b, o + 4);
      var hdr = 8;
      if (size === 1) { size = u64(b, o + 8); hdr = 16; } else if (size === 0) { size = end - o; }
      if (size < hdr || o + size > end) throw new Error('bad box ' + type);
      var box = { type: type, start: o, size: size, hdr: hdr };
      if (CONTAINERS[type]) box.children = parse(b, o + hdr, o + size);
      out.push(box);
      o += size;
    }
    return out;
  }
  function kids(box, type) { return (box && box.children || []).filter(function (c) { return c.type === type; }); }
  function kid(box, type) { return kids(box, type)[0]; }

  function concat(parts) {
    var len = 0;
    parts.forEach(function (p) { len += p.length; });
    var out = new Uint8Array(len);
    var o = 0;
    parts.forEach(function (p) { out.set(p, o); o += p.length; });
    return out;
  }
  function be32(v) { return new Uint8Array([(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]); }
  function be64(v) { var hi = Math.floor(v / 4294967296); return concat([be32(hi), be32(v - hi * 4294967296)]); }
  function mkbox(type, payload) {
    var head = concat([be32(8 + payload.length), new Uint8Array([type.charCodeAt(0), type.charCodeAt(1), type.charCodeAt(2), type.charCodeAt(3)])]);
    return concat([head, payload]);
  }
  function fullbox(type, version, flags, payloadParts) {
    return mkbox(type, concat([new Uint8Array([version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255])].concat(payloadParts)));
  }

  // Patch the duration field of mvhd/tkhd/mdhd in a copy of the box bytes.
  function withDuration(b, box, duration, offV0, offV1) {
    var bytes = b.slice(box.start, box.start + box.size);
    var body = box.hdr;
    if (bytes[body] === 1) bytes.set(be64(duration), body + offV1);
    else bytes.set(be32(Math.min(duration, 4294967295)), body + offV0);
    return bytes;
  }

  function collectSamples(b, top, trackId, trex) {
    var samples = [];
    top.forEach(function (moof) {
      if (moof.type !== 'moof') return;
      var trafs = kids(moof, 'traf');
      if (trafs.length !== 1) throw new Error('unsupported traf count');
      var traf = trafs[0];
      var tfhd = kid(traf, 'tfhd');
      var p = tfhd.start + tfhd.hdr;
      var flags = u24(b, p + 1);
      if (u32(b, p + 4) !== trackId) throw new Error('unexpected track');
      var q = p + 8;
      var base = moof.start;
      var defDur = trex.duration, defSize = trex.size, defFlags = trex.flags;
      if (flags & 0x1) { base = u64(b, q); q += 8; }
      if (flags & 0x2) q += 4;
      if (flags & 0x8) { defDur = u32(b, q); q += 4; }
      if (flags & 0x10) { defSize = u32(b, q); q += 4; }
      if (flags & 0x20) { defFlags = u32(b, q); q += 4; }
      var nextData = null;
      kids(traf, 'trun').forEach(function (trun) {
        var t = trun.start + trun.hdr;
        var version = b[t];
        var tf = u24(b, t + 1);
        var count = u32(b, t + 4);
        var r = t + 8;
        var dataPos = nextData;
        if (tf & 0x1) { dataPos = base + i32(b, r); r += 4; }
        if (dataPos === null) dataPos = base;
        var firstFlags = null;
        if (tf & 0x4) { firstFlags = u32(b, r); r += 4; }
        for (var i = 0; i < count; i++) {
          var dur = defDur, size = defSize, sflags = defFlags, cto = 0;
          if (tf & 0x100) { dur = u32(b, r); r += 4; }
          if (tf & 0x200) { size = u32(b, r); r += 4; }
          if (tf & 0x400) { sflags = u32(b, r); r += 4; } else if (i === 0 && firstFlags !== null) { sflags = firstFlags; }
          if (tf & 0x800) { cto = version ? i32(b, r) : u32(b, r); r += 4; }
          if (dataPos + size > b.length) throw new Error('sample outside file');
          samples.push({ pos: dataPos, size: size, dur: dur, sync: !(sflags & 0x10000), cto: cto });
          dataPos += size;
        }
        nextData = dataPos;
      });
    });
    if (!samples.length) throw new Error('no samples');
    return samples;
  }

  function buildStbl(b, stbl, samples, chunkOffset) {
    var stsd = kid(stbl, 'stsd');
    // stts: run-length encoded sample durations.
    var runs = [];
    samples.forEach(function (s) {
      var last = runs[runs.length - 1];
      if (last && last[1] === s.dur) last[0] += 1; else runs.push([1, s.dur]);
    });
    var stts = fullbox('stts', 0, 0, [be32(runs.length)].concat(runs.map(function (r) { return concat([be32(r[0]), be32(r[1])]); })));
    var parts = [b.slice(stsd.start, stsd.start + stsd.size), stts];
    if (samples.some(function (s) { return s.cto !== 0; })) {
      var negative = samples.some(function (s) { return s.cto < 0; });
      var cruns = [];
      samples.forEach(function (s) {
        var last = cruns[cruns.length - 1];
        if (last && last[1] === s.cto) last[0] += 1; else cruns.push([1, s.cto]);
      });
      parts.push(fullbox('ctts', negative ? 1 : 0, 0, [be32(cruns.length)].concat(cruns.map(function (r) { return concat([be32(r[0]), be32(r[1] >>> 0)]); }))));
    }
    parts.push(fullbox('stsc', 0, 0, [be32(1), be32(1), be32(samples.length), be32(1)]));
    parts.push(fullbox('stsz', 0, 0, [be32(0), be32(samples.length)].concat(samples.map(function (s) { return be32(s.size); }))));
    parts.push(fullbox('stco', 0, 0, [be32(1), be32(chunkOffset)]));
    if (samples.some(function (s) { return !s.sync; })) {
      var syncs = [];
      samples.forEach(function (s, i) { if (s.sync) syncs.push(be32(i + 1)); });
      parts.push(fullbox('stss', 0, 0, [be32(syncs.length)].concat(syncs)));
    }
    return mkbox('stbl', concat(parts));
  }

  function rebuild(b) {
    var top = parse(b, 0, b.length);
    var ftyp = top.filter(function (x) { return x.type === 'ftyp'; })[0];
    var moov = top.filter(function (x) { return x.type === 'moov'; })[0];
    if (!ftyp || !moov) throw new Error('not an mp4');
    var mvex = kid(moov, 'mvex');
    if (!mvex) return null; // already a plain MP4
    var traks = kids(moov, 'trak');
    if (traks.length !== 1) throw new Error('unsupported track count');
    var trak = traks[0];
    var tkhd = kid(trak, 'tkhd');
    var tk = tkhd.start + tkhd.hdr;
    var trackId = u32(b, tk + (b[tk] === 1 ? 20 : 12));
    var trex = { duration: 0, size: 0, flags: 0 };
    kids(mvex, 'trex').forEach(function (x) {
      var p = x.start + x.hdr;
      if (u32(b, p + 4) === trackId) trex = { duration: u32(b, p + 12), size: u32(b, p + 16), flags: u32(b, p + 20) };
    });
    var samples = collectSamples(b, top, trackId, trex);

    var mvhd = kid(moov, 'mvhd');
    var mdia = kid(trak, 'mdia');
    var mdhd = kid(mdia, 'mdhd');
    var minf = kid(mdia, 'minf');
    var stbl = kid(minf, 'stbl');
    var mv = mvhd.start + mvhd.hdr;
    var movieScale = u32(b, mv + (b[mv] === 1 ? 20 : 12));
    var md = mdhd.start + mdhd.hdr;
    var mediaScale = u32(b, md + (b[md] === 1 ? 20 : 12));
    if (!movieScale || !mediaScale) throw new Error('bad timescale');
    var mediaDuration = samples.reduce(function (sum, s) { return sum + s.dur; }, 0);
    var movieDuration = Math.round(mediaDuration * movieScale / mediaScale);

    var copy = function (box) { return b.slice(box.start, box.start + box.size); };
    var buildMoov = function (chunkOffset) {
      var minfParts = minf.children.map(function (c) { return c === stbl ? buildStbl(b, stbl, samples, chunkOffset) : copy(c); });
      var mdiaParts = mdia.children.map(function (c) {
        if (c === mdhd) return withDuration(b, mdhd, mediaDuration, 16, 24);
        if (c === minf) return mkbox('minf', concat(minfParts));
        return copy(c);
      });
      var trakParts = trak.children.map(function (c) {
        if (c === tkhd) return withDuration(b, tkhd, movieDuration, 20, 28);
        if (c === mdia) return mkbox('mdia', concat(mdiaParts));
        return copy(c);
      });
      var moovParts = moov.children.filter(function (c) { return c !== mvex; }).map(function (c) {
        if (c === mvhd) return withDuration(b, mvhd, movieDuration, 16, 24);
        if (c === trak) return mkbox('trak', concat(trakParts));
        return copy(c);
      });
      return mkbox('moov', concat(moovParts));
    };
    var ftypBytes = copy(ftyp);
    var dataLength = samples.reduce(function (sum, s) { return sum + s.size; }, 0);
    if (dataLength + 8 > 4294967295) throw new Error('too large');
    var moovLength = buildMoov(0).length;
    var newMoov = buildMoov(ftypBytes.length + moovLength + 8);
    var out = new Uint8Array(ftypBytes.length + newMoov.length + 8 + dataLength);
    out.set(ftypBytes, 0);
    out.set(newMoov, ftypBytes.length);
    var o = ftypBytes.length + newMoov.length;
    out.set(be32(8 + dataLength), o);
    out.set([0x6d, 0x64, 0x61, 0x74], o + 4); // "mdat"
    o += 8;
    samples.forEach(function (s) { out.set(b.subarray(s.pos, s.pos + s.size), o); o += s.size; });
    return out;
  }

  window.academicarDefragmentMp4 = function (blob) {
    return blob.arrayBuffer().then(function (buffer) {
      try {
        var out = rebuild(new Uint8Array(buffer));
        return out ? new Blob([out], { type: 'video/mp4' }) : blob;
      } catch (error) {
        return blob;
      }
    }, function () { return blob; });
  };
})();
