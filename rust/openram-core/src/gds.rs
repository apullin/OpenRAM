// GDSII reader + hierarchy flattener, replacing the gdsMill read path used
// by the routers (VlsiLayout/Gds2reader + getAllShapes/getAllPinShapes).
//
// Fidelity notes:
// - gdsMill builds rotation matrices with math.cos/math.sin of the sref
//   angle; for the 90-degree multiples OpenRAM emits, those carry ~1e-17
//   residues instead of exact zeros. We reproduce CPython's exact f64
//   values for radians(0/90/180/270) so flattened coordinates are
//   bit-identical to the Python path.
// - Transform composition follows vlsiLayout.addToXyTree: per hierarchy
//   level, origin/u/v are rotated, scaled (mirror-X), then translated,
//   applied in reverse path order.

#[derive(Clone, Copy, Debug, Default)]
pub struct Rect {
    pub llx: f64,
    pub lly: f64,
    pub urx: f64,
    pub ury: f64,
}

#[derive(Clone, Debug)]
pub struct Boundary {
    pub layer: i16,
    pub purpose: i16,
    /// First and third coordinate pair (gdsMill uses coordinates[0]/[2]).
    pub c0: (f64, f64),
    pub c2: (f64, f64),
}

#[derive(Clone, Debug)]
pub struct Text {
    pub layer: i16,
    pub purpose: i16,
    pub xy: (f64, f64),
    pub string: String,
}

#[derive(Clone, Debug)]
pub struct Sref {
    pub sname: String,
    pub xy: (f64, f64),
    /// STRANS reflect-about-X flag (bit 15)
    pub mirror_x: bool,
    pub angle: f64,
}

#[derive(Clone, Debug, Default)]
pub struct Structure {
    pub name: String,
    pub boundaries: Vec<Boundary>,
    pub texts: Vec<Text>,
    pub srefs: Vec<Sref>,
}

#[derive(Debug, Default)]
pub struct Layout {
    pub structures: Vec<Structure>,
    pub index: std::collections::HashMap<String, usize>,
    /// user units per database unit (GDS UNITS record, first value)
    pub user_unit: f64,
    pub root: Option<usize>,
}

// Record types
const BGNSTR: u8 = 0x05;
const STRNAME: u8 = 0x06;
const ENDSTR: u8 = 0x07;
const BOUNDARY: u8 = 0x08;
const SREF: u8 = 0x0A;
const AREF: u8 = 0x0B;
const TEXT: u8 = 0x0C;
const LAYER: u8 = 0x0D;
const DATATYPE: u8 = 0x0E;
const XY: u8 = 0x10;
const ENDEL: u8 = 0x11;
const SNAME: u8 = 0x12;
const STRING: u8 = 0x19;
const STRANS: u8 = 0x1A;
const ANGLE: u8 = 0x1C;
const TEXTTYPE: u8 = 0x16;
const UNITS: u8 = 0x03;

fn be_i16(b: &[u8]) -> i16 {
    i16::from_be_bytes([b[0], b[1]])
}

fn be_i32(b: &[u8]) -> i32 {
    i32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

/// GDSII 8-byte excess-64 real.
fn gds_real(b: &[u8]) -> f64 {
    let sign = if b[0] & 0x80 != 0 { -1.0 } else { 1.0 };
    let exponent = (b[0] & 0x7f) as i32 - 64;
    let mut mantissa: f64 = 0.0;
    for (i, &byte) in b[1..8].iter().enumerate() {
        mantissa += (byte as f64) / 256f64.powi(i as i32 + 1);
    }
    sign * mantissa * 16f64.powi(exponent)
}

impl Layout {
    pub fn parse(data: &[u8]) -> Result<Layout, String> {
        let mut layout = Layout {
            user_unit: 0.001,
            ..Default::default()
        };
        let mut cur: Option<Structure> = None;
        // element scratch
        let mut el_kind: u8 = 0;
        let mut el_layer: i16 = 0;
        let mut el_purpose: i16 = 0;
        let mut el_xy: Vec<(f64, f64)> = Vec::new();
        let mut el_sname = String::new();
        let mut el_string = String::new();
        let mut el_mirror = false;
        let mut el_angle = 0.0f64;

        let mut i = 0usize;
        let n = data.len();
        while i + 4 <= n {
            let reclen = ((data[i] as usize) << 8) | data[i + 1] as usize;
            if reclen < 4 {
                break;
            }
            let rectype = data[i + 2];
            let p = &data[i + 4..(i + reclen).min(n)];
            match rectype {
                UNITS => {
                    if p.len() >= 8 {
                        layout.user_unit = gds_real(&p[0..8]);
                    }
                }
                BGNSTR => {
                    cur = Some(Structure::default());
                }
                STRNAME => {
                    if let Some(s) = cur.as_mut() {
                        s.name = String::from_utf8_lossy(p)
                            .trim_end_matches('\0')
                            .to_string();
                    }
                }
                ENDSTR => {
                    if let Some(s) = cur.take() {
                        layout.index.insert(s.name.clone(), layout.structures.len());
                        layout.structures.push(s);
                    }
                }
                BOUNDARY | SREF | AREF | TEXT => {
                    el_kind = rectype;
                    el_layer = 0;
                    el_purpose = 0;
                    el_xy.clear();
                    el_sname.clear();
                    el_string.clear();
                    el_mirror = false;
                    el_angle = 0.0;
                }
                LAYER => el_layer = be_i16(p),
                DATATYPE | TEXTTYPE => el_purpose = be_i16(p),
                XY => {
                    el_xy.clear();
                    let mut j = 0;
                    while j + 8 <= p.len() {
                        el_xy.push((be_i32(&p[j..]) as f64, be_i32(&p[j + 4..]) as f64));
                        j += 8;
                    }
                }
                SNAME => {
                    el_sname = String::from_utf8_lossy(p)
                        .trim_end_matches('\0')
                        .to_string();
                }
                STRING => {
                    el_string = String::from_utf8_lossy(p)
                        .trim_end_matches('\0')
                        .to_string();
                }
                STRANS => el_mirror = p.len() >= 2 && (p[0] & 0x80) != 0,
                ANGLE => {
                    if p.len() >= 8 {
                        el_angle = gds_real(&p[0..8]);
                    }
                }
                ENDEL => {
                    if let Some(s) = cur.as_mut() {
                        match el_kind {
                            BOUNDARY => {
                                if el_xy.len() >= 3 {
                                    s.boundaries.push(Boundary {
                                        layer: el_layer,
                                        purpose: el_purpose,
                                        c0: el_xy[0],
                                        c2: el_xy[2],
                                    });
                                }
                            }
                            TEXT => {
                                if !el_xy.is_empty() {
                                    s.texts.push(Text {
                                        layer: el_layer,
                                        purpose: el_purpose,
                                        xy: el_xy[0],
                                        string: el_string.clone(),
                                    });
                                }
                            }
                            SREF | AREF => {
                                if !el_xy.is_empty() {
                                    s.srefs.push(Sref {
                                        sname: el_sname.clone(),
                                        xy: el_xy[0],
                                        mirror_x: el_mirror,
                                        angle: el_angle,
                                    });
                                }
                            }
                            _ => {}
                        }
                    }
                    el_kind = 0;
                }
                _ => {}
            }
            i += reclen;
        }
        layout.root = layout.find_root();
        Ok(layout)
    }

    /// deduceHierarchy: the root is the structure never referenced.
    fn find_root(&self) -> Option<usize> {
        let mut referenced = vec![false; self.structures.len()];
        for s in &self.structures {
            for r in &s.srefs {
                if let Some(&i) = self.index.get(&r.sname) {
                    referenced[i] = true;
                }
            }
        }
        // gdsMill picks the LAST unreferenced structure in file order when
        // several exist; OpenRAM designs have exactly one.
        let mut root = None;
        for (i, is_ref) in referenced.iter().enumerate() {
            if !is_ref {
                root = Some(i);
            }
        }
        root
    }
}

/// CPython's exact values for cos/sin of math.radians(0/90/180/270),
/// matching what gdsMill's numpy rotation matrices contain.
pub fn rot_cos_sin(angle_deg: f64) -> (f64, f64) {
    if angle_deg == 0.0 {
        (1.0, 0.0)
    } else if angle_deg == 90.0 {
        (6.123233995736766e-17, 1.0)
    } else if angle_deg == 180.0 {
        (-1.0, 1.2246467991473532e-16)
    } else if angle_deg == 270.0 {
        (-1.8369701987210297e-16, -1.0)
    } else {
        let r = angle_deg.to_radians();
        (r.cos(), r.sin())
    }
}

/// One flattened placement: the accumulated origin and basis vectors,
/// exactly like a vlsiLayout xyTree entry.
#[derive(Clone, Copy, Debug)]
pub struct Placement {
    pub struct_index: usize,
    pub origin: (f64, f64),
    /// u = image of (1,0), v = image of (0,1) (x components then y)
    pub u: (f64, f64),
    pub v: (f64, f64),
}

impl Layout {
    /// populateCoordinateMap: depth-first traversal composing
    /// rotate->scale->translate per level in reverse path order.
    pub fn flatten(&self) -> Vec<Placement> {
        let mut out = Vec::new();
        if let Some(root) = self.root {
            self.walk(root, (0.0, 0.0), (1.0, 0.0), (0.0, 1.0), &mut out);
        }
        out
    }

    fn walk(
        &self,
        idx: usize,
        origin: (f64, f64),
        u: (f64, f64),
        v: (f64, f64),
        out: &mut Vec<Placement>,
    ) {
        out.push(Placement {
            struct_index: idx,
            origin,
            u,
            v,
        });
        for sref in &self.structures[idx].srefs {
            let child = match self.index.get(&sref.sname) {
                Some(&c) => c,
                None => continue,
            };
            // Child-local transform: rotate by angle, mirror-X scale,
            // translate by xy. Composed with the parent transform the same
            // way the reversed matrix products in addToXyTree resolve.
            let (c, s) = rot_cos_sin(sref.angle);
            let sy = if sref.mirror_x { -1.0 } else { 1.0 };
            // local u/v after rotate+scale (column vectors of R then S)
            let lu = (c, sy * s);
            let lv = (-s, sy * c);
            // compose into parent frame
            let nu = (u.0 * lu.0 + v.0 * lu.1, u.1 * lu.0 + v.1 * lu.1);
            let nv = (u.0 * lv.0 + v.0 * lv.1, u.1 * lv.0 + v.1 * lv.1);
            let no = (
                origin.0 + u.0 * sref.xy.0 + v.0 * sref.xy.1,
                origin.1 + u.1 * sref.xy.0 + v.1 * sref.xy.1,
            );
            self.walk(child, no, nu, nv, out);
        }
    }

    /// getAllShapes(lpp) over the flattened hierarchy, in user units.
    /// A purpose of -1 matches any datatype (None purpose in Python).
    pub fn get_all_shapes(&self, layer: i16, purpose: i16) -> Vec<Rect> {
        let unit = self.user_unit;
        let mut rects = Vec::new();
        for p in self.flatten() {
            for b in &self.structures[p.struct_index].boundaries {
                if b.layer != layer {
                    continue;
                }
                if purpose >= 0 && b.purpose >= 0 && b.purpose != purpose {
                    continue;
                }
                // transformRectangle: transform both corners, re-sort.
                let t0 = transform_point(b.c0, p);
                let t2 = transform_point(b.c2, p);
                rects.push(Rect {
                    llx: t0.0.min(t2.0) * unit,
                    lly: t0.1.min(t2.1) * unit,
                    urx: t0.0.max(t2.0) * unit,
                    ury: t0.1.max(t2.1) * unit,
                });
            }
        }
        rects
    }
}

fn transform_point(pt: (f64, f64), p: Placement) -> (f64, f64) {
    (
        pt.0 * p.u.0 + pt.1 * p.v.0 + p.origin.0,
        pt.0 * p.u.1 + pt.1 * p.v.1 + p.origin.1,
    )
}
