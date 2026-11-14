// Python-compatible numerics, so the Node port of the floor-plan pipeline gives exactly the same numbers as the
// original Python implementation (demo tools/mp_plan.py and tools/calidad.py, Python 3.11).

/** Python round(x, n): correctly rounded from the exact binary value, ties to even. */
export function pyRound(x, n = 0) {
  if (!Number.isFinite(x)) return x;
  const neg = x < 0 || Object.is(x, -0), ax = Math.abs(x);
  const exact = ax.toFixed(100); // exact decimal expansion of the double (enough digits for |x| >= 2^-48)
  const [ent, frac = ""] = exact.split(".");
  let out;
  if (frac[n] === "5" && /^0*$/.test(frac.slice(n + 1))) {
    // exact tie: keep the even last digit
    const digits = ent + frac.slice(0, n);
    const last = Number(digits[digits.length - 1]);
    let big = BigInt(digits);
    if (last % 2 === 1) big += 1n;
    const s = big.toString().padStart(n + 1, "0");
    out = Number(n ? `${s.slice(0, s.length - n)}.${s.slice(s.length - n)}` : s);
  } else out = Number(ax.toFixed(n)); // toFixed rounds the exact value to the nearest (no tie here)
  return neg ? -out : out;
}

/** Python "%.nf" % x */
export const pyFixed = (x, n) => pyRound(x, n).toFixed(n);

/** Python float modulo: result has the sign of the divisor. */
export function pyMod(a, b) {
  let m = a % b;
  if (m !== 0) { if ((b < 0) !== (m < 0)) m += b; } else m = b < 0 ? -0 : 0;
  return m;
}

/** Python sum() over floats (3.11: plain left-to-right addition, starting from 0). */
export const pySum = (arr) => arr.reduce((s, v) => s + v, 0);

/** math.dist / math.hypot for 2D. */
export const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);
