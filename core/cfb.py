"""Minimal CFB (Compound File Binary / OLE2) reader - no external deps."""
import struct

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF


class CFB:
    def __init__(self, data):
        self.d = data
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not a CFB file")
        self.sector_shift = struct.unpack_from("<H", data, 30)[0]
        self.mini_shift = struct.unpack_from("<H", data, 32)[0]
        self.ssz = 1 << self.sector_shift
        self.msz = 1 << self.mini_shift
        self.num_fat = struct.unpack_from("<I", data, 44)[0]
        self.first_dir = struct.unpack_from("<I", data, 48)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 56)[0]
        self.first_minifat = struct.unpack_from("<I", data, 60)[0]
        self.num_minifat = struct.unpack_from("<I", data, 64)[0]
        self.first_difat = struct.unpack_from("<I", data, 68)[0]
        self.num_difat = struct.unpack_from("<I", data, 72)[0]
        self._build_difat()
        self._build_fat()
        self._build_minifat()
        self._parse_dir()

    # --- sector addressing ------------------------------------------------
    def sec_off(self, sid):
        return (sid + 1) * self.ssz

    def read_sector(self, sid):
        o = self.sec_off(sid)
        return self.d[o : o + self.ssz]

    # --- DIFAT / FAT ------------------------------------------------------
    def _build_difat(self):
        per = self.ssz // 4 - 1
        self.difat = list(struct.unpack_from("<109I", self.d, 76))
        sid = self.first_difat
        n = 0
        while sid not in (FREESECT, ENDOFCHAIN) and n < self.num_difat + 8:
            sec = self.read_sector(sid)
            vals = list(struct.unpack_from("<%dI" % (self.ssz // 4), sec, 0))
            self.difat.extend(vals[:per])
            sid = vals[per]
            n += 1

    def _build_fat(self):
        self.fat = []
        for sid in self.difat[: self.num_fat]:
            if sid in (FREESECT, ENDOFCHAIN):
                continue
            sec = self.read_sector(sid)
            self.fat.extend(struct.unpack_from("<%dI" % (self.ssz // 4), sec, 0))

    def chain(self, start, fat):
        out = []
        sid = start
        seen = set()
        while sid not in (ENDOFCHAIN, FREESECT) and sid < len(fat):
            if sid in seen:
                break
            seen.add(sid)
            out.append(sid)
            sid = fat[sid]
        return out

    def read_chain(self, start):
        return b"".join(self.read_sector(s) for s in self.chain(start, self.fat))

    def _build_minifat(self):
        self.minifat = []
        if self.first_minifat in (FREESECT, ENDOFCHAIN):
            return
        raw = self.read_chain(self.first_minifat)
        self.minifat = list(struct.unpack_from("<%dI" % (len(raw) // 4), raw, 0))

    def read_mini(self, start, size):
        out = []
        sid = start
        seen = set()
        while sid not in (ENDOFCHAIN, FREESECT) and sid < len(self.minifat):
            if sid in seen:
                break
            seen.add(sid)
            o = sid * self.msz
            out.append(self.d[o : o + self.msz])
            sid = self.minifat[sid]
        return b"".join(out)[:size]

    # --- directory --------------------------------------------------------
    def _parse_dir(self):
        raw = self.read_chain(self.first_dir)
        self.entries = []
        for i in range(0, len(raw), 128):
            e = raw[i : i + 128]
            if len(e) < 128:
                break
            nlen = struct.unpack_from("<H", e, 64)[0]
            name = e[: max(0, nlen - 2)].decode("utf-16-le", errors="replace")
            self.entries.append(
                {
                    "index": i // 128,
                    "name": name,
                    "type": e[66],
                    "left": struct.unpack_from("<I", e, 68)[0],
                    "right": struct.unpack_from("<I", e, 72)[0],
                    "child": struct.unpack_from("<I", e, 76)[0],
                    "start": struct.unpack_from("<I", e, 116)[0],
                    "size": struct.unpack_from("<I", e, 120)[0],
                }
            )
        # mini stream root entry = entry 0
        self.ministream = b""
        if self.entries:
            r = self.entries[0]
            if r["size"] > 0 and r["start"] not in (ENDOFCHAIN, FREESECT):
                self.ministream = self.read_chain(r["start"])[: r["size"]]

    def read_entry(self, e):
        if e["type"] != 2:
            return b""
        if e["size"] == 0:
            return b""
        if e["size"] < self.mini_cutoff:
            o = e["start"] * self.msz
            return self.ministream[o : o + e["size"]]
        return self.read_chain(e["start"])[: e["size"]]

    def streams(self):
        return [e for e in self.entries if e["type"] == 2 and e["size"] > 0]

    def open(self, name):
        for e in self.entries:
            if e["name"] == name and e["type"] == 2:
                return self.read_entry(e)
        return None
