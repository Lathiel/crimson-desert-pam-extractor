#!/usr/bin/env python3
"""
Crimson Desert PAM Extractor v3.2
Vertex format: uint16 quantized, dequantization: bmin + v/65535 * (bmax-bmin)
Two layout modes:
  - local  (mesh PAM):   per-mesh vertex buffer, stride auto-detected, indices follow verts, 0-based
  - global (prefab PAM): shared buffers, GLOBAL_VERT_BASE=3068, PAM_IDX_OFF=0x19840
PAMLOD: LOD mesh file — multiple quality levels of the same mesh, sequential geometry blocks.
"""

import struct, sys, argparse, os, re, zlib, io, math, random
from pathlib import Path
from datetime import datetime

PAM_MAGIC         = b'PAR '
SUBMESH_TABLE     = 0x410
SUBMESH_STRIDE    = 0x218
HDR_MESH_COUNT    = 0x10
HDR_GEOM_OFF      = 0x3C
HDR_BBOX_MIN      = 0x14
HDR_BBOX_MAX      = 0x20
SUBMESH_TEX_OFF   = 0x10
SUBMESH_MAT_OFF   = 0x110
GLOBAL_VERT_BASE  = 3068
PAM_IDX_OFF       = 0x19840


def dequant(v_uint16, mn, mx):
    """uint16 -> float: bmin + v/65535 * (bmax-bmin)"""
    return mn + (v_uint16 / 65535.0) * (mx - mn)


def dequant_int16(v_int16, mn, mx):
    """int16 -> float (legacy global-buffer format): bmin + (v+32768)/65536 * (bmax-bmin)"""
    return mn + ((v_int16 + 32768) / 65536.0) * (mx - mn)


class PamParser:
    def __init__(self, path):
        self.path = Path(path)
        self.data = open(path, 'rb').read()
        self.meshes = []
        self.bbox_min = self.bbox_max = None
        self._parse()

    def _find_local_layout(self, geom_off, voff_bytes, n_verts, n_idx):
        """Detect vertex stride for per-mesh layout where index buffer follows vertex data.
        Returns (stride, idx_off) or (None, None) if not found."""
        d = self.data
        for stride in [6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 36, 40]:
            vert_start = geom_off + voff_bytes
            idx_off = vert_start + n_verts * stride
            if idx_off + n_idx * 2 > len(d):
                continue
            valid = all(
                struct.unpack_from('<H', d, idx_off + j * 2)[0] < n_verts
                for j in range(n_idx)
            )
            if valid:
                return stride, idx_off
        return None, None

    def _parse(self):
        d = self.data
        if d[:4] != PAM_MAGIC:
            raise ValueError(f"Bad magic: {d[:4]!r}, expected b'PAR '")

        self.bbox_min = struct.unpack_from('<fff', d, HDR_BBOX_MIN)
        self.bbox_max = struct.unpack_from('<fff', d, HDR_BBOX_MAX)
        geom_off   = struct.unpack_from('<I', d, HDR_GEOM_OFF)[0]
        mesh_count = struct.unpack_from('<I', d, HDR_MESH_COUNT)[0]
        idx_avail  = (len(d) - PAM_IDX_OFF) // 2

        # Pre-read all submesh table entries
        raw = []
        for i in range(mesh_count):
            off = SUBMESH_TABLE + i * SUBMESH_STRIDE
            raw.append(dict(
                i=i, off=off,
                nv=struct.unpack_from('<I', d, off)[0],
                ni=struct.unpack_from('<I', d, off + 4)[0],
                ve=struct.unpack_from('<I', d, off + 8)[0],   # vertex element offset
                ie=struct.unpack_from('<I', d, off + 12)[0],  # index element offset
                tex=d[off+SUBMESH_TEX_OFF:off+SUBMESH_TEX_OFF+256].split(b'\x00')[0].decode('ascii','replace'),
                mat=d[off+SUBMESH_MAT_OFF:off+SUBMESH_MAT_OFF+256].split(b'\x00')[0].decode('ascii','replace'),
            ))

        # Detect combined-buffer layout: multi-submesh PAMs where voff/ioff are
        # vertex/index element counts into shared arrays (not byte offsets).
        # Signature: entry[i].ve == sum(nv[:i]) and entry[i].ie == sum(ni[:i]).
        is_combined = False
        if mesh_count > 1:
            ve_acc = ie_acc = 0
            is_combined = True
            for r in raw:
                if r['ve'] != ve_acc or r['ie'] != ie_acc:
                    is_combined = False
                    break
                ve_acc += r['nv']
                ie_acc += r['ni']

        if is_combined:
            self._parse_combined_buffer(raw, geom_off)
            return

        # ── Per-submesh path (single-mesh or independent-buffer multi-mesh) ──
        for r in raw:
            i, n_verts, n_idx, voff, ioff = r['i'], r['nv'], r['ni'], r['ve'], r['ie']
            tex, mat = r['tex'], r['mat']

            # Try per-mesh (local) layout first: index buffer immediately follows vertex data
            stride, idx_off_local = self._find_local_layout(geom_off, voff, n_verts, n_idx)

            if stride is not None:
                # Local format: uint16 positions, float16 UVs at +8/+10, indices follow vertex data
                indices = [struct.unpack_from('<H', d, idx_off_local + j * 2)[0] for j in range(n_idx)]
                if not indices:
                    continue

                unique  = sorted(set(indices))
                idx_map = {gi: li for li, gi in enumerate(unique)}

                verts = []
                uvs   = []
                has_uv = stride >= 12
                for gi in unique:
                    foff = geom_off + voff + gi * stride
                    if foff + 6 > len(d):
                        break
                    xu, yu, zu = struct.unpack_from('<HHH', d, foff)
                    verts.append((
                        dequant(xu, self.bbox_min[0], self.bbox_max[0]),
                        dequant(yu, self.bbox_min[1], self.bbox_max[1]),
                        dequant(zu, self.bbox_min[2], self.bbox_max[2]),
                    ))
                    if has_uv and foff + 12 <= len(d):
                        u = struct.unpack_from('<e', d, foff + 8)[0]
                        v = struct.unpack_from('<e', d, foff + 10)[0]
                        uvs.append((u, v))

                faces = []
                for j in range(0, n_idx - 2, 3):
                    a, b, c = indices[j], indices[j+1], indices[j+2]
                    if a in idx_map and b in idx_map and c in idx_map:
                        faces.append((idx_map[a], idx_map[b], idx_map[c]))

            else:
                # Global format (prefab PAMs): shared vertex/index buffers with hardcoded offsets
                if ioff + n_idx > idx_avail:
                    print(f"  [!] Mesh {i} ({mat}): indices out of PAM bounds -- skipping (mesh 4 and 5 are in PAMLOD)")
                    continue

                indices = [struct.unpack_from('<H', d, PAM_IDX_OFF + (ioff+j)*2)[0] for j in range(n_idx)]
                if not indices:
                    continue

                unique  = sorted(set(indices))
                idx_map = {gi: li for li, gi in enumerate(unique)}

                verts = []
                uvs   = []
                for gi in unique:
                    li   = gi - GLOBAL_VERT_BASE
                    foff = geom_off + li * 6
                    if foff + 6 > len(d):
                        break
                    xi, yi, zi = struct.unpack_from('<hhh', d, foff)
                    verts.append((
                        dequant_int16(xi, self.bbox_min[0], self.bbox_max[0]),
                        dequant_int16(yi, self.bbox_min[1], self.bbox_max[1]),
                        dequant_int16(zi, self.bbox_min[2], self.bbox_max[2]),
                    ))

                faces = []
                for j in range(0, n_idx - 2, 3):
                    a, b, c = indices[j], indices[j+1], indices[j+2]
                    if a in idx_map and b in idx_map and c in idx_map:
                        faces.append((idx_map[a], idx_map[b], idx_map[c]))

            self.meshes.append({
                'name':    f'mesh_{i:02d}_{mat or str(i)}',
                'role':    self._guess_role(mat, i, n_verts),
                'verts':   verts,
                'uvs':     uvs,
                'faces':   faces,
                'texture': tex,
                'material': mat,
            })

    def _parse_combined_buffer(self, raw, geom_off):
        """Handle multi-submesh PAMs with a shared (combined) vertex+index buffer.
        voff/ioff in the table are element-count offsets, not byte offsets."""
        d = self.data
        total_verts = sum(r['nv'] for r in raw)
        total_idx   = sum(r['ni'] for r in raw)
        avail       = len(d) - geom_off

        # Find stride: all vertex data + all index data must fit
        target = (avail - total_idx * 2) / total_verts if total_verts else 0
        valid_strides = [6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 36, 40]
        stride = min(valid_strides, key=lambda s: abs(s - target))
        if geom_off + total_verts * stride + total_idx * 2 > len(d):
            return  # doesn't fit — bail out

        idx_base = geom_off + total_verts * stride   # byte offset of combined index buffer

        for r in raw:
            nv, ni     = r['nv'], r['ni']
            vert_base  = geom_off + r['ve'] * stride   # ve is vertex element offset (count)
            idx_off    = idx_base  + r['ie'] * 2       # ie is index element offset (count)
            tex, mat   = r['tex'], r['mat']
            i          = r['i']

            indices = [struct.unpack_from('<H', d, idx_off + j * 2)[0] for j in range(ni)]
            if not indices:
                continue

            unique  = sorted(set(indices))
            idx_map = {gi: li for li, gi in enumerate(unique)}

            verts, uvs = [], []
            has_uv = stride >= 12
            bmin, bmax = self.bbox_min, self.bbox_max
            for gi in unique:
                foff = vert_base + gi * stride
                if foff + 6 > len(d):
                    break
                xu, yu, zu = struct.unpack_from('<HHH', d, foff)
                verts.append((
                    dequant(xu, bmin[0], bmax[0]),
                    dequant(yu, bmin[1], bmax[1]),
                    dequant(zu, bmin[2], bmax[2]),
                ))
                if has_uv and foff + 12 <= len(d):
                    u = struct.unpack_from('<e', d, foff + 8)[0]
                    v = struct.unpack_from('<e', d, foff + 10)[0]
                    uvs.append((u, v))

            faces = []
            for j in range(0, ni - 2, 3):
                a, b, c = indices[j], indices[j + 1], indices[j + 2]
                if a in idx_map and b in idx_map and c in idx_map:
                    faces.append((idx_map[a], idx_map[b], idx_map[c]))

            self.meshes.append({
                'name':     f'mesh_{i:02d}_{mat or str(i)}',
                'role':     self._guess_role(mat, i, nv),
                'verts':    verts,
                'uvs':      uvs,
                'faces':    faces,
                'texture':  tex,
                'material': mat,
            })

    @staticmethod
    def _guess_role(mat, idx, n_verts):
        """Guess the submesh role within a prefab."""
        if 'butter' in mat:    return 'ingredient:butter'
        if 'radish' in mat:    return 'ingredient:radish'
        if 'lattuce' in mat or 'lettuce' in mat: return 'ingredient:lettuce'
        if 'onion' in mat:     return 'ingredient:onion'
        if mat == 'cd_food_01': return 'ingredient:food_base'
        # Largest mesh (same name as file) = main container
        if n_verts > 1000:     return 'main_container'
        return f'submesh_{idx}'


# ── PAMLOD constants ────────────────────────────────────────────────────────
PAMLOD_LOD_COUNT    = 0x00   # uint32: number of LOD levels
PAMLOD_GEOM_OFF     = 0x04   # uint32: byte offset where geometry data begins
PAMLOD_BBOX_MIN     = 0x10   # 3 float32: bounding box minimum
PAMLOD_BBOX_MAX     = 0x1C   # 3 float32: bounding box maximum
PAMLOD_ENTRY_TABLE  = 0x50   # LOD entry table starts here (fixed offset)
# Within each LOD entry (offset relative to the texture-field start):
#   tex_start - 0x10  → n_verts
#   tex_start - 0x0C  → n_idx
#   tex_start - 0x08  → voff  (unused for parsing, geometry is sequential)
#   tex_start - 0x04  → ioff
#   tex_start + 0x000 → texture name  (256-byte null-padded field)
#   tex_start + 0x100 → material name (256-byte null-padded field)
# Entry stride is variable per file; we locate entries by scanning for .dds\0.


class PamlodParser:
    """
    Parses PAMLOD (LOD mesh) files.
    Geometry is stored as sequential vertex+index blocks, one per LOD level.
    LOD 0 = highest quality; LOD n = most reduced.
    Uses the same local-layout detection as PamParser (stride scan).
    """
    def __init__(self, path):
        self.path      = Path(path)
        self.data      = open(path, 'rb').read()
        self.lod_meshes = []   # one mesh dict per LOD level (or None if parse failed)
        self.bbox_min  = None
        self.bbox_max  = None
        self._parse()

    def _parse(self):
        d = self.data
        lod_count = struct.unpack_from('<I', d, PAMLOD_LOD_COUNT)[0]
        geom_off  = struct.unpack_from('<I', d, PAMLOD_GEOM_OFF)[0]
        if lod_count == 0 or geom_off == 0 or geom_off >= len(d):
            return
        self.bbox_min = struct.unpack_from('<fff', d, PAMLOD_BBOX_MIN)
        self.bbox_max = struct.unpack_from('<fff', d, PAMLOD_BBOX_MAX)

        # ── Locate LOD entries by anchoring on .dds texture strings ─────────
        # Each LOD entry: n_verts @ tex_start-0x10, n_idx @ -0x0C,
        #                 voff @ -0x08, ioff @ -0x04, tex @ +0x000, mat @ +0x100
        entries = []
        search_region = d[PAMLOD_ENTRY_TABLE:geom_off]
        for m in re.finditer(rb'[^\x00]{1,255}\.dds\x00', search_region):
            tex_start = PAMLOD_ENTRY_TABLE + m.start()
            nv_off = tex_start - 0x10
            if nv_off < PAMLOD_ENTRY_TABLE:
                continue
            n_verts = struct.unpack_from('<I', d, nv_off)[0]
            n_idx   = struct.unpack_from('<I', d, nv_off + 0x04)[0]
            if not (1 <= n_verts <= 131072 and n_idx > 0 and n_idx % 3 == 0):
                continue
            voff = struct.unpack_from('<I', d, tex_start - 0x08)[0]
            ioff = struct.unpack_from('<I', d, tex_start - 0x04)[0]
            tex = d[tex_start : tex_start + 256].split(b'\x00')[0].decode('ascii', 'replace')
            mat_start = tex_start + 0x100
            mat = (d[mat_start : mat_start + 256].split(b'\x00')[0]
                   .decode('ascii', 'replace') if mat_start < geom_off else '')
            entries.append({'n_verts': n_verts, 'n_idx': n_idx, 'voff': voff, 'ioff': ioff,
                            'tex_start': tex_start, 'texture': tex, 'material': mat})

        entries.sort(key=lambda e: e['tex_start'])

        # ── Group consecutive entries into per-LOD groups ────────────────────
        # Entries with cumulative voff/ioff belong to the same LOD's combined buffer.
        # (Single-submesh LODs have voff=0/ioff=0 and each forms its own group.)
        lod_groups = []
        cur_group, ve_acc, ie_acc = [], 0, 0
        for e in entries:
            if e['voff'] == ve_acc and e['ioff'] == ie_acc:
                cur_group.append(e)
                ve_acc += e['n_verts']
                ie_acc += e['n_idx']
            else:
                if cur_group:
                    lod_groups.append(cur_group)
                cur_group = [e]
                ve_acc = e['n_verts']
                ie_acc = e['n_idx']
        if cur_group:
            lod_groups.append(cur_group)
        lod_groups = lod_groups[:lod_count]

        if not lod_groups:
            return

        bmin, bmax = self.bbox_min, self.bbox_max

        # ── Extract geometry sequentially, one LOD group at a time ──────────
        cur = geom_off
        for lod_i, group in enumerate(lod_groups):
            total_nv = sum(e['n_verts'] for e in group)
            total_ni = sum(e['n_idx']   for e in group)

            # Stride scan: combined index buffer starts at base + total_nv*stride
            found_base = found_stride = found_idx_off = None
            for pad in range(0, 64, 2):
                base = cur + pad
                for stride in [6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 36, 40]:
                    cand = base + total_nv * stride
                    if cand + total_ni * 2 > len(d):
                        continue
                    if all(struct.unpack_from('<H', d, cand + j * 2)[0] < total_nv
                           for j in range(total_ni)):
                        found_base   = base
                        found_stride = stride
                        found_idx_off = cand
                        break
                if found_base is not None:
                    break

            if found_base is None:
                self.lod_meshes.append(None)
                cur += 2
                continue

            # ── Parse and merge all submeshes in this LOD group ──────────────
            all_verts, all_uvs, all_faces = [], [], []
            vert_offset = 0
            has_uv = found_stride >= 12
            for e in group:
                nv_e = e['n_verts']
                ni_e = e['n_idx']
                vert_base_e = found_base     + e['voff'] * found_stride
                idx_off_e   = found_idx_off  + e['ioff'] * 2

                indices = [struct.unpack_from('<H', d, idx_off_e + j * 2)[0] for j in range(ni_e)]
                unique  = sorted(set(indices))
                idx_map = {gi: li + vert_offset for li, gi in enumerate(unique)}

                for gi in unique:
                    foff = vert_base_e + gi * found_stride
                    if foff + 6 > len(d):
                        break
                    xu, yu, zu = struct.unpack_from('<HHH', d, foff)
                    all_verts.append((
                        dequant(xu, bmin[0], bmax[0]),
                        dequant(yu, bmin[1], bmax[1]),
                        dequant(zu, bmin[2], bmax[2]),
                    ))
                    if has_uv and foff + 12 <= len(d):
                        u = struct.unpack_from('<e', d, foff + 8)[0]
                        v = struct.unpack_from('<e', d, foff + 10)[0]
                        all_uvs.append((u, v))

                for j in range(0, ni_e - 2, 3):
                    a, b, c = indices[j], indices[j + 1], indices[j + 2]
                    if a in idx_map and b in idx_map and c in idx_map:
                        all_faces.append((idx_map[a], idx_map[b], idx_map[c]))

                vert_offset += len(unique)

            mat = group[0]['material'] or f'lod{lod_i}'
            all_textures = [e['texture'] for e in group if e['texture']]
            self.lod_meshes.append({
                'name':     f'lod{lod_i:02d}_{mat}',
                'role':     f'lod_{lod_i}',
                'verts':    all_verts,
                'uvs':      all_uvs,
                'faces':    all_faces,
                'texture':  group[0]['texture'],
                'textures': all_textures,   # all submesh textures in this LOD group
                'material': mat,
            })
            cur = found_idx_off + total_ni * 2


class ObjExporter:
    def __init__(self, parser, out_dir, scale=1.0):
        self.p     = parser
        self.out   = Path(out_dir)
        self.scale = scale
        os.makedirs(str(self.out), exist_ok=True)

    def export(self, base):
        obj = self.out / f'{base}.obj'
        mtl = self.out / f'{base}.mtl'
        self._write_mtl(mtl)
        self._write_obj(obj, mtl.name, base)
        return obj, mtl

    def _write_mtl(self, path):
        seen, lines = set(), ['# Crimson Desert MTL — cd_extractor v3.1', '']
        for m in self.p.meshes:
            n = m['material'] or m['name']
            if n in seen: continue
            seen.add(n)
            lines += [f'newmtl {n}', 'Ka 1 1 1', 'Kd 1 1 1', 'Ks 0 0 0', 'Ns 10', 'd 1', 'illum 2']
            if m['texture']: lines.append(f'map_Kd {m["texture"]}')
            lines.append('')
        path.write_text('\n'.join(lines), encoding='utf-8')

    def _write_obj(self, path, mtl_name, base):
        lines = [f'# Crimson Desert PAM — {base}', f'# {len(self.p.meshes)} submesh(es)', f'mtllib {mtl_name}', '']
        go = 1   # global vertex offset (1-based)
        gto = 1  # global UV offset (1-based)
        s = self.scale
        for m in self.p.meshes:
            mat = m['material'] or m['name']
            uvs = m.get('uvs', [])
            lines += [f'o {m["name"]}', f'usemtl {mat}']
            for x, y, z in m['verts']:
                lines.append(f'v {x*s:.6f} {y*s:.6f} {z*s:.6f}')
            for u, v in uvs:
                lines.append(f'vt {u:.6f} {1.0 - v:.6f}')
            lines.append('s off')
            if uvs:
                for a, b, c in m['faces']:
                    va, vb, vc = a+go, b+go, c+go
                    ta, tb, tc = a+gto, b+gto, c+gto
                    lines.append(f'f {va}/{ta} {vb}/{tb} {vc}/{tc}')
            else:
                for a, b, c in m['faces']:
                    lines.append(f'f {a+go} {b+go} {c+go}')
            lines.append('')
            go  += len(m['verts'])
            gto += len(uvs)
        path.write_text('\n'.join(lines), encoding='utf-8')


class ObjSplitExporter(ObjExporter):
    def export(self, base):
        results = []
        for i, m in enumerate(self.p.meshes):
            name = f'{base}_mesh{i:02d}'
            obj  = self.out / f'{name}.obj'
            mtl  = self.out / f'{name}.mtl'
            mat  = m['material'] or name
            mtl.write_text('\n'.join([
                f'# {mat}', f'newmtl {mat}', 'Ka 1 1 1', 'Kd 1 1 1', 'Ks 0 0 0',
                'Ns 10', 'd 1', 'illum 2',
                f'map_Kd {m["texture"]}' if m['texture'] else ''
            ]), encoding='utf-8')
            obj_lines = [f'# {m["name"]} ({m["role"]})', f'mtllib {mtl.name}',
                         f'o {m["name"]}', f'usemtl {mat}', '']
            uvs = m.get('uvs', [])
            s = self.scale
            for x, y, z in m['verts']:
                obj_lines.append(f'v {x*s:.6f} {y*s:.6f} {z*s:.6f}')
            for u, v in uvs:
                obj_lines.append(f'vt {u:.6f} {1.0 - v:.6f}')
            obj_lines.append('s off')
            if uvs:
                for a, b, c in m['faces']:
                    obj_lines.append(f'f {a+1}/{a+1} {b+1}/{b+1} {c+1}/{c+1}')
            else:
                for a, b, c in m['faces']:
                    obj_lines.append(f'f {a+1} {b+1} {c+1}')
            obj.write_text('\n'.join(obj_lines), encoding='utf-8')
            results.append((obj, mtl))
        return results


# ── Binary FBX 7.4 helpers ──────────────────────────────────────────────────

class _FbxId(int):
    """int subclass that _fbx_enc always encodes as int64 — required for FBX node
    IDs in Objects and Connections (Blender checks props_type == b'LL')."""


def _fbx_enc(v):
    """Encode one FBX binary property value to bytes."""
    if isinstance(v, bool):
        return b'C' + struct.pack('B', int(v))
    if isinstance(v, int):
        # _FbxId or out-of-int32-range → int64; small plain ints → int32
        # (Blender's elem_props_get_enum asserts INT32 for enum P properties)
        if isinstance(v, _FbxId) or v < -2_147_483_648 or v > 2_147_483_647:
            return b'L' + struct.pack('<q', v)
        return b'I' + struct.pack('<i', v)
    if isinstance(v, float):
        return b'D' + struct.pack('<d', v)
    if isinstance(v, str):
        e = v.encode('utf-8')
        return b'S' + struct.pack('<I', len(e)) + e
    if isinstance(v, bytes):
        return b'R' + struct.pack('<I', len(v)) + v
    if isinstance(v, list):
        if not v:
            return b'i' + struct.pack('<III', 0, 0, 0)
        if isinstance(v[0], float):
            raw = struct.pack(f'<{len(v)}d', *v)
            cmp = zlib.compress(raw, 1)
            enc, cl = (1, len(cmp)) if len(cmp) < len(raw) else (0, len(raw))
            return b'd' + struct.pack('<III', len(v), enc, cl) + (cmp if enc else raw)
        raw = struct.pack(f'<{len(v)}i', *v)
        cmp = zlib.compress(raw, 1)
        enc, cl = (1, len(cmp)) if len(cmp) < len(raw) else (0, len(raw))
        return b'i' + struct.pack('<III', len(v), enc, cl) + (cmp if enc else raw)
    raise TypeError(f'Unsupported FBX property type: {type(v)}')


def _fbx_node(buf, name, props=(), children=None):
    """Write one FBX binary 7.4 node record into a BytesIO buffer."""
    nb = name.encode('ascii')
    pb = b''.join(_fbx_enc(p) for p in props)
    ph = buf.tell()                          # placeholder position for EndOffset
    buf.write(b'\x00\x00\x00\x00')          # EndOffset (patched after body)
    buf.write(struct.pack('<I', len(props)))
    buf.write(struct.pack('<I', len(pb)))
    buf.write(struct.pack('B',  len(nb)))
    buf.write(nb)
    buf.write(pb)
    if children is not None:
        children()
        buf.write(b'\x00' * 13)             # null-record terminator for nested list
    end = buf.tell()
    buf.seek(ph)
    buf.write(struct.pack('<I', end))
    buf.seek(end)


def _fbx_smooth_normals(verts, faces):
    """Compute per-vertex smooth normals."""
    n = len(verts)
    nrm = [[0.0, 0.0, 0.0] for _ in verts]
    for a, b, c in faces:
        if a >= n or b >= n or c >= n:
            continue  # skip degenerate face with out-of-range index
        ax, ay, az = verts[a];  bx, by, bz = verts[b];  cx, cy, cz = verts[c]
        ex, ey, ez = bx-ax, by-ay, bz-az
        fx, fy, fz = cx-ax, cy-ay, cz-az
        nx = ey*fz - ez*fy;  ny = ez*fx - ex*fz;  nz = ex*fy - ey*fx
        for i in (a, b, c):
            nrm[i][0] += nx;  nrm[i][1] += ny;  nrm[i][2] += nz
    result = []
    for nx, ny, nz in nrm:
        L = math.sqrt(nx*nx + ny*ny + nz*nz)
        result.append((nx/L, ny/L, nz/L) if L > 1e-8 else (0.0, 1.0, 0.0))
    return result


class FbxExporter:
    """
    Exports to FBX Binary 7.4 format.
    No external libraries required.
    Compatible with Blender, Maya, 3ds Max, Unity 5+, Unreal Engine 4+.
    """
    _FOOTER_MAGIC = bytes([
        0xf8, 0x5a, 0x8c, 0x6a, 0xde, 0xf5, 0xd9, 0x7e,
        0xec, 0xe9, 0x0c, 0xe3, 0x75, 0x8f, 0x29, 0x0b,
    ])

    def __init__(self, parser, out_dir, scale=1.0):
        self.p       = parser
        self.out     = Path(out_dir)
        self.scale   = scale
        self._id_ctr = 3_000_000_000   # > int32 max → always encoded as int64
        os.makedirs(str(self.out), exist_ok=True)

    def _uid(self):
        self._id_ctr += 1
        return _FbxId(self._id_ctr)

    def export(self, base):
        fbx = self.out / f'{base}.fbx'
        self._write(fbx, self.p.meshes)
        return fbx

    def _write(self, path, meshes):
        buf = io.BytesIO()
        W   = _fbx_node
        now = datetime.now()

        # Skip degenerate submeshes (0 vertices or 0 faces)
        meshes = [m for m in meshes if m and len(m.get('verts', [])) > 0 and len(m.get('faces', [])) > 0]
        if not meshes:
            return  # nothing valid to export

        geom_ids  = [self._uid() for _ in meshes]
        model_ids = [self._uid() for _ in meshes]
        mat_names = list(dict.fromkeys(m['material'] or m['name'] for m in meshes))
        mat_ids   = {n: self._uid() for n in mat_names}
        doc_id    = self._uid()

        # ── file header ─────────────────────────────────────────────────────────
        buf.write(b'Kaydara FBX Binary  \x00\x1a\x00')
        buf.write(struct.pack('<I', 7400))

        # ── FBXHeaderExtension ──────────────────────────────────────────────────
        def hdr():
            W(buf, 'FBXHeaderVersion', [1003])
            W(buf, 'FBXVersion',       [7400])
            W(buf, 'EncryptionType',   [0])
            def ts():
                W(buf,'Version',[1000]);  W(buf,'Year',[now.year])
                W(buf,'Month',[now.month]);  W(buf,'Day',[now.day])
                W(buf,'Hour',[now.hour]);  W(buf,'Minute',[now.minute])
                W(buf,'Second',[now.second]);  W(buf,'Millisecond',[0])
            W(buf, 'CreationTimeStamp', children=ts)
            W(buf, 'Creator', ['Crimson Desert PAM Extractor v3.1'])
        W(buf, 'FBXHeaderExtension', children=hdr)

        W(buf, 'FileId',       [bytes(random.randint(0, 255) for _ in range(16))])
        W(buf, 'CreationTime', ['1970-01-01 10:00:00:000'])
        W(buf, 'Creator',      ['Crimson Desert PAM Extractor v3.1'])

        # ── GlobalSettings ──────────────────────────────────────────────────────
        def gsettings():
            W(buf, 'Version', [1000])
            def p70():
                W(buf,'P',['UpAxis','int','Integer','',1])
                W(buf,'P',['UpAxisSign','int','Integer','',1])
                W(buf,'P',['FrontAxis','int','Integer','',2])
                W(buf,'P',['FrontAxisSign','int','Integer','',1])
                W(buf,'P',['CoordAxis','int','Integer','',0])
                W(buf,'P',['CoordAxisSign','int','Integer','',1])
                W(buf,'P',['OriginalUpAxis','int','Integer','',1])
                W(buf,'P',['OriginalUpAxisSign','int','Integer','',1])
                W(buf,'P',['UnitScaleFactor','double','Number','',1.0])
                W(buf,'P',['OriginalUnitScaleFactor','double','Number','',1.0])
                W(buf,'P',['AmbientColor','ColorRGB','Color','',0.0,0.0,0.0])
                W(buf,'P',['DefaultCamera','KString','','','Producer Perspective'])
                W(buf,'P',['TimeMode','enum','','',11])
                W(buf,'P',['TimeSpanStart','KTime','Time','',0])
                W(buf,'P',['TimeSpanStop','KTime','Time','',0])
                W(buf,'P',['CustomFrameRate','double','Number','',-1.0])
            W(buf, 'Properties70', children=p70)
        W(buf, 'GlobalSettings', children=gsettings)

        # ── Documents ───────────────────────────────────────────────────────────
        def docs():
            W(buf, 'Count', [1])
            W(buf, 'Document', [doc_id, '', 'Scene'],
              children=lambda: W(buf, 'RootNode', [0]))
        W(buf, 'Documents', children=docs)
        W(buf, 'References')

        # ── Definitions ─────────────────────────────────────────────────────────
        def defs():
            total = 1 + len(meshes) * 2 + len(mat_names)
            W(buf, 'Version', [100]);  W(buf, 'Count', [total])
            for otype, cnt in [('GlobalSettings', 1), ('Model', len(meshes)),
                               ('Geometry', len(meshes)), ('Material', len(mat_names))]:
                def ot(c=cnt): W(buf, 'Count', [c])
                W(buf, 'ObjectType', [otype], children=ot)
        W(buf, 'Definitions', children=defs)

        # ── Objects ─────────────────────────────────────────────────────────────
        def objects():
            for idx, m in enumerate(meshes):
                gid    = geom_ids[idx];  mid   = model_ids[idx]
                mname  = m['name']
                verts  = m['verts'];     uvs   = m.get('uvs', [])
                faces  = m['faces']
                has_uv = len(uvs) == len(verts) and bool(uvs)
                s = self.scale
                flat_v = [c * s for xyz in verts for c in xyz]
                flat_i = [v for a, b, c in faces for v in (a, b, ~c)]
                norms  = _fbx_smooth_normals(verts, faces)
                # flat_n: per-vertex (IndexToDirect — index array expands per face-corner)
                flat_n = [c for n in norms for c in n]

                def write_geom(gid=gid, mname=mname, flat_v=flat_v, flat_i=flat_i,
                               flat_n=flat_n, uvs=uvs, faces=faces, has_uv=has_uv):
                    def body():
                        W(buf, 'Vertices',          [flat_v])
                        W(buf, 'PolygonVertexIndex', [flat_i])
                        W(buf, 'GeometryVersion',    [124])
                        # normals: IndexToDirect matches what Blender writes/reads
                        nrm_idx = [vi for a, b, fc in faces for vi in (a, b, fc)]
                        def nl(flat_n=flat_n, nrm_idx=nrm_idx):
                            W(buf,'Version',[102]);  W(buf,'Name',[''])
                            W(buf,'MappingInformationType',['ByPolygonVertex'])
                            W(buf,'ReferenceInformationType',['IndexToDirect'])
                            W(buf,'Normals',[flat_n])
                            W(buf,'NormalsIndex',[nrm_idx])
                        W(buf, 'LayerElementNormal', [0], children=nl)
                        if has_uv:
                            # UV array per-vertex; index array expands per face-corner
                            fuv    = [val for u, v in uvs for val in (u, 1.0 - v)]
                            uv_idx = [vi for a, b, fc in faces for vi in (a, b, fc)]
                            def ul(fuv=fuv, uv_idx=uv_idx):
                                W(buf,'Version',[101]);  W(buf,'Name',['UVMap'])
                                W(buf,'MappingInformationType',['ByPolygonVertex'])
                                W(buf,'ReferenceInformationType',['IndexToDirect'])
                                W(buf,'UV',[fuv])
                                W(buf,'UVIndex',[uv_idx])
                            W(buf, 'LayerElementUV', [0], children=ul)
                        # LayerElementMaterial: AllSame — required by Blender importer
                        def ml():
                            W(buf,'Version',[101]);  W(buf,'Name',[''])
                            W(buf,'MappingInformationType',['AllSame'])
                            W(buf,'ReferenceInformationType',['IndexToDirect'])
                            W(buf,'Materials',[[0]])
                        W(buf, 'LayerElementMaterial', [0], children=ml)
                        def layer():
                            W(buf, 'Version', [100])
                            def le_n():
                                W(buf,'Type',['LayerElementNormal']);  W(buf,'TypedIndex',[0])
                            W(buf, 'LayerElement', children=le_n)
                            if has_uv:
                                def le_u():
                                    W(buf,'Type',['LayerElementUV']);  W(buf,'TypedIndex',[0])
                                W(buf, 'LayerElement', children=le_u)
                            def le_m():
                                W(buf,'Type',['LayerElementMaterial']);  W(buf,'TypedIndex',[0])
                            W(buf, 'LayerElement', children=le_m)
                        W(buf, 'Layer', [0], children=layer)
                    W(buf, 'Geometry', [gid, mname + '\x00\x01Geometry', 'Mesh'], children=body)
                write_geom()

                def write_model(mid=mid, mname=mname):
                    def body():
                        W(buf, 'Version', [232])
                        W(buf, 'MultiLayer', [0])
                        W(buf, 'MultiTake',  [0])
                        def p70():
                            W(buf,'P',['Lcl Translation','Lcl Translation','','A',0.0,0.0,0.0])
                            W(buf,'P',['Lcl Rotation','Lcl Rotation','','A',0.0,0.0,0.0])
                            W(buf,'P',['Lcl Scaling','Lcl Scaling','','A',100.0,100.0,100.0])
                        W(buf, 'Properties70', children=p70)
                        W(buf, 'Shading',  [True])
                        W(buf, 'Culling',  ['CullingOff'])
                    W(buf, 'Model', [mid, mname + '\x00\x01Model', 'Mesh'], children=body)
                write_model()

            for mn in mat_names:
                _id = mat_ids[mn]
                def write_mat(mn=mn, _id=_id):
                    def body():
                        W(buf,'Version',[102]);  W(buf,'ShadingModel',['phong'])
                        W(buf,'MultiLayer',[0])
                        def p70():
                            W(buf,'P',['DiffuseColor','Color','','A',0.8,0.8,0.8])
                            W(buf,'P',['DiffuseFactor','Number','','A',1.0])
                            W(buf,'P',['AmbientColor','Color','','A',0.2,0.2,0.2])
                            W(buf,'P',['SpecularColor','Color','','A',0.2,0.2,0.2])
                            W(buf,'P',['Shininess','Number','','A',20.0])
                            W(buf,'P',['Opacity','Number','','A',1.0])
                        W(buf,'Properties70',children=p70)
                    W(buf, 'Material', [_id, mn + '\x00\x01Material', ''], children=body)
                write_mat()
        W(buf, 'Objects', children=objects)

        # ── Connections ─────────────────────────────────────────────────────────
        def conns():
            for idx, m in enumerate(meshes):
                mn = m['material'] or m['name']
                W(buf, 'C', ['OO', geom_ids[idx], model_ids[idx]])
                W(buf, 'C', ['OO', model_ids[idx], _FbxId(0)])  # 0=scene root, must be int64
                W(buf, 'C', ['OO', mat_ids[mn], model_ids[idx]])
        W(buf, 'Connections', children=conns)

        # ── Takes (expected by some parsers) ────────────────────────────────────
        W(buf, 'Takes', children=lambda: W(buf, 'Current', ['']))

        # ── Footer ──────────────────────────────────────────────────────────────
        buf.write(b'\x00' * 13)                        # null-record: end of root
        pos = buf.tell()
        buf.write(b'\x00' * ((16 - pos % 16) % 16))   # pad to 16-byte boundary
        buf.write(struct.pack('<I', 7400))              # version
        buf.write(b'\x00' * 120)                       # padding
        buf.write(self._FOOTER_MAGIC)
        path.write_bytes(buf.getvalue())


class FbxSplitExporter(FbxExporter):
    """One binary FBX file per submesh."""
    def export(self, base):
        results = []
        for i, m in enumerate(self.p.meshes):
            fbx = self.out / f'{base}_mesh{i:02d}.fbx'
            self._write(fbx, [m])
            results.append(fbx)
        return results


# ── PAMLOD export helpers ────────────────────────────────────────────────────

class PamlodFbxExporter(FbxExporter):
    """Export each PAMLOD LOD level as a separate binary FBX file."""
    def __init__(self, lod_parser, out_dir, scale=1.0):
        super().__init__(None, out_dir, scale=scale)
        self.lod = lod_parser

    def export(self, base):
        results = []
        for lod_i, mesh in enumerate(self.lod.lod_meshes):
            if mesh is None:
                print(f"  [!] LOD {lod_i}: geometry parse failed -- skipped")
                continue
            fbx = self.out / f'{base}_lod{lod_i}.fbx'
            self._write(fbx, [mesh])
            results.append(fbx)
        return results


class PamlodObjExporter(ObjExporter):
    """Export each PAMLOD LOD level as a separate OBJ file."""
    def __init__(self, lod_parser, out_dir, scale=1.0):
        super().__init__(None, out_dir, scale=scale)
        self.lod = lod_parser

    def export(self, base):
        results = []
        for lod_i, mesh in enumerate(self.lod.lod_meshes):
            if mesh is None:
                print(f"  [!] LOD {lod_i}: geometry parse failed -- skipped")
                continue
            name = f'{base}_lod{lod_i}'
            obj  = self.out / f'{name}.obj'
            mtl  = self.out / f'{name}.mtl'
            mat  = mesh['material'] or name
            mtl.write_text('\n'.join([
                f'# {mat}', f'newmtl {mat}', 'Ka 1 1 1', 'Kd 1 1 1', 'Ks 0 0 0',
                'Ns 10', 'd 1', 'illum 2',
                f'map_Kd {mesh["texture"]}' if mesh['texture'] else ''
            ]), encoding='utf-8')
            uvs  = mesh.get('uvs', [])
            s     = self.scale
            lines = [f'# LOD{lod_i}  {mesh["name"]}', f'mtllib {mtl.name}',
                     f'o {mesh["name"]}', f'usemtl {mat}', '']
            for x, y, z in mesh['verts']:
                lines.append(f'v {x*s:.6f} {y*s:.6f} {z*s:.6f}')
            for u, v in uvs:
                lines.append(f'vt {u:.6f} {1.0 - v:.6f}')
            lines.append('s off')
            if uvs:
                for a, b, c in mesh['faces']:
                    lines.append(f'f {a+1}/{a+1} {b+1}/{b+1} {c+1}/{c+1}')
            else:
                for a, b, c in mesh['faces']:
                    lines.append(f'f {a+1} {b+1} {c+1}')
            obj.write_text('\n'.join(lines), encoding='utf-8')
            results.append((obj, mtl))
        return results


def print_info(pam):
    bb, bx = pam.bbox_min, pam.bbox_max
    print(f"\n{'='*60}")
    print(f"  PREFAB INFO -- {pam.path.name}")
    print(f"{'='*60}")
    print(f"  Type: PREFAB (one file = multiple mesh components)")
    print(f"  BBox: ({bb[0]:.4f},{bb[1]:.4f},{bb[2]:.4f}) -> ({bx[0]:.4f},{bx[1]:.4f},{bx[2]:.4f})")
    print(f"  Size: {(bx[0]-bb[0])*100:.1f}cm x {(bx[1]-bb[1])*100:.1f}cm x {(bx[2]-bb[2])*100:.1f}cm")
    print(f"  Submeshes: {len(pam.meshes)}")
    print()
    tv = tf = 0
    for i, m in enumerate(pam.meshes):
        v, f = len(m['verts']), len(m['faces'])
        tv += v; tf += f
        print(f"  [{i}] {m['name']}")
        print(f"       Role:    {m['role']}")
        print(f"       {v} vertices  |  {f} triangles")
        print(f"       Texture: {m['texture'] or '(none)'}")
    print(f"\n  TOTAL: {tv} vertices, {tf} triangles")
    # Unique textures summary
    seen_tex = {}
    for m in pam.meshes:
        base = m['texture'].replace('.dds','') if m['texture'] else None
        mat  = m['material'] or ''
        if base and base not in seen_tex:
            seen_tex[base] = mat
    if seen_tex:
        print(f"\n  Textures referenced in file:")
        for base, mat in seen_tex.items():
            print(f"    {base}.dds   (material: {mat})")
    print(f"{'='*60}\n")


def print_lod_info(lod):
    bb, bx = lod.bbox_min, lod.bbox_max
    valid  = [m for m in lod.lod_meshes if m is not None]
    print(f"\n{'='*60}")
    print(f"  LOD INFO -- {lod.path.name}")
    print(f"{'='*60}")
    print(f"  LOD levels : {len(lod.lod_meshes)}")
    print(f"  BBox: ({bb[0]:.4f},{bb[1]:.4f},{bb[2]:.4f}) -> ({bx[0]:.4f},{bx[1]:.4f},{bx[2]:.4f})")
    print(f"  Size: {(bx[0]-bb[0])*100:.1f}cm x {(bx[1]-bb[1])*100:.1f}cm x {(bx[2]-bb[2])*100:.1f}cm")
    print()
    for i, m in enumerate(lod.lod_meshes):
        if m is None:
            print(f"  [LOD{i}] PARSE FAILED")
            continue
        print(f"  [LOD{i}] {len(m['verts'])} vertices  |  {len(m['faces'])} triangles")
        print(f"          Texture: {m['texture'] or '(none)'}")
    if valid:
        tv = sum(len(m['verts']) for m in valid)
        tf = sum(len(m['faces']) for m in valid)
        print(f"\n  Parsed: {len(valid)}/{len(lod.lod_meshes)} LOD levels")
        print(f"  LOD 0 (best): {len(valid[0]['verts'])} vertices, {len(valid[0]['faces'])} triangles")
        # Unique textures across all LODs (use 'textures' list if available)
        seen_tex = {}
        for m in valid:
            tex_list = m.get('textures') or ([m['texture']] if m['texture'] else [])
            mat = m['material'] or ''
            for tex in tex_list:
                base = tex.replace('.dds', '') if tex else None
                if base and base not in seen_tex:
                    seen_tex[base] = mat
        if seen_tex:
            print(f"\n  Textures referenced in file:")
            for base, mat in seen_tex.items():
                print(f"    {base}.dds   (material: {mat})")
    print(f"{'='*60}\n")


def write_textures_txt(meshes, base, out_dir, source_path=None):
    """Write <base>_textures.txt listing all textures referenced by the exported mesh(es).
    Checks the source file's folder for known texture variants (_n, _sp, _d, _r, _emissive).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f'{base}_textures.txt'
    src_dir  = Path(source_path).parent if source_path else None

    # Collect unique textures: base_name -> list of (mesh_name, material_name)
    seen = {}
    for m in meshes:
        if m is None:
            continue
        tex_list = m.get('textures') or ([m['texture']] if m.get('texture') else [])
        mat   = m.get('material') or m.get('name', '')
        mname = m.get('name', mat)
        for tex in tex_list:
            if not tex:
                continue
            key = tex.lower().replace('.dds', '')
            if key not in seen:
                seen[key] = []
            seen[key].append((mname, mat))

    lines = [
        f'Textures referenced by: {base}',
        f'Generated by: Crimson Desert PAM Extractor v3.2',
        '=' * 60,
        '',
    ]

    if not seen:
        lines.append('(no textures found)')
    else:
        lines.append(f'Unique textures: {len(seen)}')
        lines.append('')
        VARIANTS = [('_n', 'Normal    '), ('_sp', 'Specular  '), ('_d', 'Detail    '), ('_r', 'Roughness '), ('_emissive', 'Emissive  ')]
        for tex_base, users in seen.items():
            user_str = ', '.join(mname for mname, _ in users[:5])
            lines.append(f'  [{tex_base}]')
            lines.append(f'    Used by  : {user_str}')
            if src_dir:
                albedo_status = 'FOUND' if (src_dir / (tex_base + '.dds')).exists() else 'not in source folder'
                lines.append(f'    Albedo   : {tex_base}.dds  [{albedo_status}]')
                for suf, label in VARIANTS:
                    candidate = tex_base + suf + '.dds'
                    status = 'FOUND' if (src_dir / candidate).exists() else 'not in source folder'
                    lines.append(f'    {label}: {candidate}  [{status}]')
            else:
                lines.append(f'    Albedo   : {tex_base}.dds')
                for suf, label in VARIANTS:
                    lines.append(f'    {label}: {tex_base}{suf}.dds')
            lines.append('')

    lines.append('=' * 60)
    txt_path.write_text('\n'.join(lines), encoding='utf-8')
    return txt_path


def copy_textures(meshes, source_path, out_dir):
    """Copy all referenced DDS textures (albedo + variants) from the source folder to out_dir."""
    import shutil
    src_dir = Path(source_path).parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    VARIANTS = ['', '_n', '_sp', '_d', '_r', '_emissive']

    seen = set()
    for m in meshes:
        if m is None:
            continue
        tex_list = m.get('textures') or ([m['texture']] if m.get('texture') else [])
        for tex in tex_list:
            if not tex:
                continue
            seen.add(tex.lower().replace('.dds', ''))

    copied, missing = [], []
    for tex_base in seen:
        for suf in VARIANTS:
            fname = tex_base + suf + '.dds'
            src = src_dir / fname
            if src.exists():
                dst = out_dir / fname
                if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                    shutil.copy2(str(src), str(dst))
                copied.append(fname)
            elif suf == '':
                missing.append(fname)

    return copied, missing


def main():
    ap = argparse.ArgumentParser(
        description='Crimson Desert PAM/PAMLOD Extractor v3.2',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cd_extractor.py model.pam
  cd_extractor.py model.pam  --format fbx -o ./output
  cd_extractor.py model.pamlod
  cd_extractor.py model.pamlod --format fbx -o ./output
  cd_extractor.py model.pam  --split -o ./meshes
  cd_extractor.py model.pam  --info-only
        """
    )
    ap.add_argument('input')
    ap.add_argument('-o', '--output', default='./output')
    ap.add_argument('--format', choices=['obj', 'fbx'], default='obj',
                    help='Output format: obj (default) or fbx')
    ap.add_argument('--split',     action='store_true', help='One file per submesh (PAM only)')
    ap.add_argument('--info-only', action='store_true')
    ap.add_argument('--open-output', action='store_true', help='Open output folder in Explorer when done')
    ap.add_argument('--copy-textures', action='store_true', help='Copy referenced DDS textures from source folder to output folder')
    ap.add_argument('--scale', type=float, default=1.0, help='Multiply all vertex coordinates by this factor (Blender default: 100)')
    args = ap.parse_args()

    ext = Path(args.input).suffix.lower()
    is_pamlod = (ext == '.pamlod')

    print(f"\nParsing: {args.input}")

    # ── PAMLOD path ──────────────────────────────────────────────────────────
    if is_pamlod:
        try:
            lod = PamlodParser(args.input)
        except Exception as e:
            print(f"ERROR: {e}"); sys.exit(1)

        print_lod_info(lod)

        if args.info_only or not any(m for m in lod.lod_meshes):
            return

        base    = Path(args.input).stem
        use_fbx = args.format == 'fbx'
        exp     = PamlodFbxExporter(lod, args.output, scale=args.scale) if use_fbx else PamlodObjExporter(lod, args.output, scale=args.scale)
        results = exp.export(base)
        ext_str = 'FBX' if use_fbx else 'OBJ'
        print(f"Exported {len(results)} {ext_str} file(s) -> {args.output}/")
        for f in results:
            fname = f[0].name if isinstance(f, tuple) else (f.name if hasattr(f, 'name') else str(f))
            print(f"  {fname}")
        txt = write_textures_txt(lod.lod_meshes, base, args.output, args.input)
        print(f"  {txt.name}")
        if args.copy_textures:
            copied, missing = copy_textures(lod.lod_meshes, args.input, args.output)
            if copied:
                print(f"  Textures copied: {len(copied)}")
                for f in copied: print(f"    {f}")
            if missing:
                print(f"  Textures NOT FOUND: {len(missing)}")
                for f in missing: print(f"    {f}")
        print("\nDone!")
        if args.open_output:
            import subprocess
            subprocess.Popen(['explorer', str(Path(args.output).resolve())])
        return

    # ── PAM path ─────────────────────────────────────────────────────────────
    try:
        pam = PamParser(args.input)
    except Exception as e:
        print(f"ERROR: {e}"); sys.exit(1)

    print_info(pam)

    if args.info_only or not pam.meshes:
        return

    base = Path(args.input).stem
    use_fbx = args.format == 'fbx'
    if args.split:
        exp     = FbxSplitExporter(pam, args.output, scale=args.scale) if use_fbx else ObjSplitExporter(pam, args.output, scale=args.scale)
        results = exp.export(base)
        ext_str = 'FBX' if use_fbx else 'OBJ'
        print(f"Exported {len(results)} {ext_str} file(s) -> {args.output}/")
        for f in results:
            print(f"  {f.name if hasattr(f, 'name') else f}")
    else:
        if use_fbx:
            exp = FbxExporter(pam, args.output, scale=args.scale)
            fbx = exp.export(base)
            print(f"Exported:\n  {fbx}")
        else:
            exp      = ObjExporter(pam, args.output, scale=args.scale)
            obj, mtl = exp.export(base)
            print(f"Exported:\n  {obj}\n  {mtl}")

    txt = write_textures_txt(pam.meshes, base, args.output, args.input)
    print(f"  {txt.name}")
    if args.copy_textures:
        copied, missing = copy_textures(pam.meshes, args.input, args.output)
        if copied:
            print(f"  Textures copied: {len(copied)}")
            for f in copied: print(f"    {f}")
        if missing:
            print(f"  Textures NOT FOUND: {len(missing)}")
            for f in missing: print(f"    {f}")
    print("\nDone!")
    if args.open_output:
        import subprocess
        subprocess.Popen(['explorer', str(Path(args.output).resolve())])


if __name__ == '__main__':
    main()
