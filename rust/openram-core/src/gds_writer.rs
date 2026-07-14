// GDSII serializer: a byte-exact port of gdsMill's Gds2writer for the
// records OpenRAM emits (header, boundaries, srefs, texts). Record
// presence follows gdsMill's truthiness rules: STRANS is written whenever
// the flags field is not "" (the default [0,0,0] list is truthy, so
// generated elements always carry it), while MAG/ANGLE appear only when
// set. Dates are passed in by the caller.

use crate::gds::{Layout, Structure};

fn record(out: &mut Vec<u8>, tag: [u8; 2], payload: &[u8]) {
    let len = (payload.len() + 4) as u16;
    out.extend_from_slice(&len.to_be_bytes());
    out.extend_from_slice(&tag);
    out.extend_from_slice(payload);
}

fn pad_name(name: &str) -> Vec<u8> {
    let mut bytes = name.as_bytes().to_vec();
    if bytes.len() % 2 != 0 {
        bytes.push(0);
    }
    bytes
}

/// gds2writer.ibmDataFromIeeeDouble, ported literally. Python's shifts are
/// arbitrary precision, so the intermediate mantissa needs 128 bits.
fn gds_real_bytes(value: f64) -> [u8; 8] {
    let data = value.to_bits() as i64;
    let sign = ((data >> 63) & 0x01) as u128;
    let mut exponent = ((data >> 52) & 0x7ff) - 1023;
    let mut mantissa: u128 = (data as u64 as u128) << 12;
    if value == 0.0 {
        mantissa = 0;
        exponent = 0;
        // sign forced to 0 in the reference
        let word: u64 = 0;
        return word.to_be_bytes();
    } else {
        mantissa >>= 1;
        mantissa |= 0x8000000000000000;
        exponent += 1;
        for _ in 0..((-exponent) & 3) {
            mantissa >>= 1;
            mantissa &= 0x7fffffffffffffff;
        }
        exponent = ((exponent + 3) >> 2) + 64;
    }
    let word: u64 = ((sign as u64) << 63)
        | ((exponent as u64) << 56)
        | (((mantissa >> 8) as u64) & 0xffffffffffffff);
    word.to_be_bytes()
}

fn dates_payload(dates: &[i16; 12]) -> Vec<u8> {
    let mut p = Vec::with_capacity(24);
    for d in dates {
        p.extend_from_slice(&d.to_be_bytes());
    }
    p
}

fn write_structure(out: &mut Vec<u8>, s: &Structure, dates: &[i16; 12]) {
    record(out, [0x05, 0x02], &dates_payload(dates));
    record(out, [0x06, 0x06], &pad_name(&s.name));

    for b in &s.boundaries {
        record(out, [0x08, 0x00], &[]);
        record(out, [0x0D, 0x02], &b.layer.to_be_bytes());
        record(out, [0x0E, 0x02], &b.purpose.to_be_bytes());
        let mut xy = Vec::with_capacity(b.coords.len() * 8);
        for &(x, y) in &b.coords {
            xy.extend_from_slice(&(x as i32).to_be_bytes());
            xy.extend_from_slice(&(y as i32).to_be_bytes());
        }
        record(out, [0x10, 0x03], &xy);
        record(out, [0x11, 0x00], &[]);
    }

    for sref in &s.srefs {
        record(out, [0x0A, 0x00], &[]);
        if !sref.sname.is_empty() {
            record(out, [0x12, 0x06], &pad_name(&sref.sname));
        }
        if let Some(mirror) = sref.strans {
            let flags: u16 = if mirror { 1 << 15 } else { 0 };
            record(out, [0x1A, 0x01], &flags.to_be_bytes());
        }
        if let Some(mag) = sref.mag {
            record(out, [0x1B, 0x05], &gds_real_bytes(mag));
        }
        if let Some(angle) = sref.angle {
            record(out, [0x1C, 0x05], &gds_real_bytes(angle));
        }
        let mut xy = Vec::with_capacity(8);
        xy.extend_from_slice(&(sref.xy.0 as i32).to_be_bytes());
        xy.extend_from_slice(&(sref.xy.1 as i32).to_be_bytes());
        record(out, [0x10, 0x03], &xy);
        record(out, [0x11, 0x00], &[]);
    }

    for t in &s.texts {
        record(out, [0x0C, 0x00], &[]);
        record(out, [0x0D, 0x02], &t.layer.to_be_bytes());
        record(out, [0x16, 0x02], &t.purpose.to_be_bytes());
        if let Some(mirror) = t.strans {
            let flags: u16 = if mirror { 1 << 15 } else { 0 };
            record(out, [0x1A, 0x01], &flags.to_be_bytes());
        }
        if let Some(mag) = t.mag {
            record(out, [0x1B, 0x05], &gds_real_bytes(mag));
        }
        if let Some(angle) = t.angle {
            record(out, [0x1C, 0x05], &gds_real_bytes(angle));
        }
        let mut xy = Vec::with_capacity(8);
        xy.extend_from_slice(&(t.xy.0 as i32).to_be_bytes());
        xy.extend_from_slice(&(t.xy.1 as i32).to_be_bytes());
        record(out, [0x10, 0x03], &xy);
        if !t.string.is_empty() {
            record(out, [0x19, 0x06], &pad_name(&t.string));
        }
        record(out, [0x11, 0x00], &[]);
    }

    record(out, [0x07, 0x00], &[]);
}

impl Layout {
    /// Serialize the layout: gds2writer.writeGds2 record for record.
    /// `dates` fills BGNLIB and every BGNSTR (validation masks them).
    pub fn write_gds(&self, dates: &[i16; 12], library_name: &str, gds_version: i16) -> Vec<u8> {
        let mut out: Vec<u8> = Vec::with_capacity(1 << 20);
        record(&mut out, [0x00, 0x02], &gds_version.to_be_bytes());
        record(&mut out, [0x01, 0x02], &dates_payload(dates));
        record(&mut out, [0x02, 0x06], &pad_name(library_name));
        // UNITS: gds2writer emits units[0] and then
        // (units[0]*1e-6/units[1])*units[1], in that exact fp op order.
        let db_units = (self.user_unit * 1e-6 / self.meter_unit) * self.meter_unit;
        let mut units = Vec::with_capacity(16);
        units.extend_from_slice(&gds_real_bytes(self.user_unit));
        units.extend_from_slice(&gds_real_bytes(db_units));
        record(&mut out, [0x03, 0x05], &units);
        for s in &self.structures {
            write_structure(&mut out, s, dates);
        }
        record(&mut out, [0x04, 0x00], &[]);
        out
    }
}
