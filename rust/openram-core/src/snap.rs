/// CPython-compatible `round(x, ndigits)`: correctly rounded decimal
/// rounding of the exact binary value, ties to even. Rust's fixed-precision
/// float formatting performs the same correctly-rounded conversion, so a
/// format/parse round-trip reproduces CPython bit-for-bit.
pub fn py_round(x: f64, ndigits: usize) -> f64 {
    // Fast path: if x is already exact at this precision, formatting can be
    // skipped. Scaling by a power of ten is exact only when the scaled value
    // round-trips, which we verify before trusting it.
    let scale = 10f64.powi(ndigits as i32);
    let scaled = x * scale;
    if scaled == scaled.trunc() && scaled.abs() < 1e15 && scaled / scale == x {
        return x;
    }
    format!("{:.*}", ndigits, x).parse().unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_cpython_examples() {
        // Values cross-checked against CPython round().
        assert_eq!(py_round(2.675, 2), 2.67); // 2.675 is 2.67499... in binary
        assert_eq!(py_round(0.125, 2), 0.12); // exact tie -> even
        assert_eq!(py_round(0.135, 2), 0.14);
        assert_eq!(py_round(1.2345, 4), 1.2345);
        assert_eq!(py_round(-2.675, 2), -2.67);
        assert_eq!(py_round(1.235, 3), 1.235);
    }
}
