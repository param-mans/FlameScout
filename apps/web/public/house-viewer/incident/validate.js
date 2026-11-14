// IncidentOverlay validator: port of packages/schemas/overlay/validate_overlay.py, rule by rule and with the same messages
// (Python repr, truthiness and Unicode word boundaries are reproduced). validate(overlay, planIndex) -> list of errors,
// empty when valid. Where Python would raise (malformed structure, e.g. occupants: null), this returns one error
// "overlay could not be validated: ..." instead, so the overlay is rejected either way.
// Known limit: JSON numbers like 2.0 arrive in JS as 2, so a float where an int is required passes here (Python rejects it).

const STATUS = new Set(['verified', 'inferred', 'reported', 'unverified', 'contradicted'])
const LOC_KINDS = new Set(['room', 'anchor', 'floor', 'unknown'])
const ANCHOR_POS = new Set(['center', 'near_door', 'near_window', 'near_stairs']) // no orientation in the contract (no cardinal sides)
const NEAR_NEEDS_REF = { near_door: 'openings', near_window: 'openings', near_stairs: 'stairs' }
const FIRE_KINDS = new Set(['smoke', 'fire', 'heat'])
const HAZARD_KINDS = new Set(['gas', 'chemical', 'battery', 'electrical', 'structural', 'other'])
const OBJ_TYPES = new Set(['rescue', 'recon'])
const TARGET_KINDS = new Set(['occupant', 'occupant_group', 'room', 'floor'])
const WAYPOINT_KINDS = new Set(['room', 'opening', 'stairs', 'exterior'])
const FORBIDDEN_KEYS = new Set(['x', 'y', 'z', 'lat', 'lon', 'latitude', 'longitude', 'coordinates', 'polygon', 'points'])
// Python: re.compile(r"\b(must|immediately|do it now|ordered|100\s*%|guaranteed)\b", re.I) on str (Unicode \b).
// No lookbehind (Safari < 16.4 cannot parse it): every start position (in code points) is tried with each anchored
// alternative, and the word boundary is checked by hand on both sides, like Python's \b.
const WORD = /[\p{L}\p{N}_]/u
const ALTS = ['must', 'immediately', 'do it now', 'ordered', '100\\s*%', 'guaranteed'].map((a) => new RegExp('^(?:' + a + ')', 'iu'))
const isWord = (c) => c !== undefined && WORD.test(c)
export const DIRECTIVE = {
  test(text) {
    const cps = [...String(text)]
    for (let i = 0; i < cps.length; i++) {
      if (isWord(cps[i - 1]) === isWord(cps[i])) continue // no boundary before position i
      const rest = cps.slice(i).join('')
      for (const re of ALTS) {
        const m = re.exec(rest)
        if (!m) continue
        const end = i + [...m[0]].length
        if (isWord(cps[end - 1]) !== isWord(cps[end])) return true
      }
    }
    return false
  },
}

// ---------------------------------------------------------------- Python semantics
const isDict = (o) => o !== null && typeof o === 'object' && !Array.isArray(o)
const isInt = (v) => (typeof v === 'number' && Number.isInteger(v)) || typeof v === 'boolean' // bool is an int in Python
const isNum = (v) => typeof v === 'number' || typeof v === 'boolean'
const num = (v) => (typeof v === 'boolean' ? Number(v) : v)
const truthy = (v) => !(v === undefined || v === null || v === false || v === 0 || v === '' || (Array.isArray(v) && !v.length) || (isDict(v) && !Object.keys(v).length))
const get = (o, k, d) => { if (!isDict(o)) throw new TypeError(`'${pyType(o)}' object has no attribute 'get'`); return k in o ? o[k] : d }
const iter = (v) => { if (Array.isArray(v)) return v; if (typeof v === 'string') return [...v]; if (isDict(v)) return Object.keys(v); throw new TypeError(`'${pyType(v)}' object is not iterable`) }
const pyType = (v) => (v === null || v === undefined ? 'NoneType' : Array.isArray(v) ? 'list' : typeof v === 'object' ? 'dict' : typeof v === 'string' ? 'str' : typeof v === 'boolean' ? 'bool' : Number.isInteger(v) ? 'int' : 'float')
function reprStr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'"
  let out = ''
  for (const ch of s) {
    const c = ch.codePointAt(0)
    if (ch === '\\') out += '\\\\'
    else if (ch === q) out += '\\' + q
    else if (ch === '\n') out += '\\n'
    else if (ch === '\r') out += '\\r'
    else if (ch === '\t') out += '\\t'
    else if (c < 0x20 || c === 0x7f) out += '\\x' + c.toString(16).padStart(2, '0')
    else if (c >= 0x80 && c < 0xa0) out += '\\x' + c.toString(16).padStart(2, '0')
    else out += ch
  }
  return q + out + q
}
function reprNum(n) {
  if (Number.isInteger(n)) return String(n)
  const s = String(n)
  return /e/.test(s) ? s.replace(/e([+-])(\d)$/, 'e$10$2') : s
}
export function repr(v) {
  if (v === undefined || v === null) return 'None'
  if (v === true) return 'True'
  if (v === false) return 'False'
  if (typeof v === 'string') return reprStr(v)
  if (typeof v === 'number') return reprNum(v)
  if (Array.isArray(v)) return '[' + v.map(repr).join(', ') + ']'
  return '{' + Object.entries(v).map(([k, x]) => repr(k) + ': ' + repr(x)).join(', ') + '}'
}
const pyStr = (v) => (typeof v === 'string' ? v : repr(v))
const sorted = (set) => repr([...set].sort())
// x in set / x in dict: lists and dicts are unhashable in Python (TypeError), so they make validation fail here too
const hashable = (v) => { if (v !== null && typeof v === 'object') throw new TypeError(`unhashable type: '${pyType(v)}'`); return v }
const inSet = (set, v) => set.has(hashable(v))

// datetime.fromisoformat (CPython 3.11) after .replace("Z", "+00:00"): only whether it parses
const DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
const leap = (y) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
function isoWeekOk(y, w, d) {
  if (w < 1 || d < 1 || d > 7) return false
  if (w <= 52) return true
  if (w > 53) return false
  const jan4 = new Date(Date.UTC(y, 0, 4)).getUTCDay() || 7 // ISO year has 53 weeks if Jan 1 is Thu, or Wed in leap years
  const jan1 = ((jan4 - 3 + 6) % 7) + 1
  return jan1 === 4 || (leap(y) && jan1 === 3)
}
function dateOk(s) {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s) || /^(\d{4})(\d{2})(\d{2})$/.exec(s)
  if (m) { const y = +m[1], mo = +m[2], d = +m[3]; return y >= 1 && mo >= 1 && mo <= 12 && d >= 1 && d <= (mo === 2 && leap(y) ? 29 : DAYS[mo - 1]) }
  m = /^(\d{4})-W(\d{2})(?:-(\d))?$/.exec(s) || /^(\d{4})W(\d{2})(\d)?$/.exec(s)
  if (m) return +m[1] >= 1 && isoWeekOk(+m[1], +m[2], m[3] ? +m[3] : 1)
  return false
}
// HH[:MM[:SS[.fff]]] or HHMM[SS[.fff]]; emptyFraction: CPython accepts "SS." (no digits) when a time zone follows
const HMS = (emptyFraction) => new RegExp(`^(\\d{2})(?:(:?)(\\d{2})(?:\\2(\\d{2})(?:[.,](\\d${emptyFraction ? '*' : '+'}))?)?)?$`)
function hmsOk(s, emptyFraction = false) {
  const m = HMS(emptyFraction).exec(s)
  return !!m && +m[1] <= 23 && (m[3] === undefined || +m[3] <= 59) && (m[4] === undefined || +m[4] <= 59)
}
// time zone offset: components are not range-checked one by one, only the total must be under 24 h (timezone())
function tzOk(tz) {
  if ([0, 1, 3].includes(tz.length)) return false
  const m = HMS(false).exec(tz)
  if (!m) return false
  const total = +m[1] * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0) + (m[5] ? Number('0.' + m[5]) : 0)
  return total < 86400
}
function timeOk(t) {
  const i = t.search(/[+-]/)
  if (i < 0) return hmsOk(t)
  return hmsOk(t.slice(0, i), true) && tzOk(t.slice(i + 1))
}
function sepIndex(s) {
  const n = s.length
  if (n === 7) return 7
  if (s[4] === '-') {
    if (s[5] === 'W') {
      if (n > 8 && s[8] === '-') { if (n === 9) return -1; if (n > 10 && /\d/.test(s[10])) return 8; return 10 }
      return 8
    }
    return 10
  }
  if (s[4] === 'W') {
    let idx = 7
    while (idx < n && /\d/.test(s[idx])) idx++
    if (idx < 9) return idx
    return idx % 2 === 0 ? 7 : 8
  }
  return 8
}
export function isoOk(v) {
  const s = pyStr(v).replace(/Z/g, '+00:00')
  if (s.length < 7) return false
  const k = sepIndex(s)
  if (k < 0) return false
  const dstr = s.slice(0, k), tstr = s.slice(k + 1)
  if (!dateOk(dstr)) return false
  if (s.length > k && !tstr) return false
  return !tstr || timeOk(tstr)
}

// ---------------------------------------------------------------- walk_keys
function* walkKeys(o, path = '') {
  if (isDict(o)) for (const k of Object.keys(o)) { yield [path + '.' + k, k]; yield* walkKeys(o[k], path + '.' + k) }
  else if (Array.isArray(o)) for (let i = 0; i < o.length; i++) yield* walkKeys(o[i], `${path}[${i}]`)
}

// ---------------------------------------------------------------- validate
export function validate(ov, plan) {
  const errs = []
  try { run(ov, plan, errs) } catch (e) { errs.push(`overlay could not be validated: ${e.message}`) }
  return errs
}

function run(ov, plan, errs) {
  const E = (m) => errs.push(m)
  const keysOf = (k) => { const v = get(plan, k, k === 'rooms' || k === 'openings' ? {} : []); return new Set(iter(v)) }
  const rooms = keysOf('rooms'), openings = keysOf('openings'), stairs = keysOf('stairs'), floors = keysOf('floors')

  for (const k of ['incident_id', 'model_id', 'version', 'as_of']) if (!(k in ov)) E('missing top-level field: ' + k)
  if (!isInt(get(ov, 'version')) || num(get(ov, 'version', 0)) < 1) E('version must be an integer >= 1')
  if (!isoOk(get(ov, 'as_of', ''))) E('as_of must be an ISO 8601 timestamp')

  for (const [p, k] of walkKeys(ov)) if (FORBIDDEN_KEYS.has(k)) E(`${p}: raw geometry or coordinates are not allowed in agent output (use ids and anchors)`)

  const ids = new Map()
  const reg = (i, where) => {
    if (!truthy(i)) E(where + ': missing id')
    else if (ids.has(hashable(i))) E(`${where}: duplicate id ${repr(i)} (also used in ${ids.get(i)})`)
    else ids.set(i, where)
  }
  const common = (e, where) => {
    reg(get(e, 'id'), where)
    if (!STATUS.has(hashable(get(e, 'status')))) E(`${where}: status must be one of ${sorted(STATUS)}`)
    const c = get(e, 'confidence')
    if (!isNum(c) || !(0 <= num(c) && num(c) <= 1)) E(where + ': confidence must be a number between 0 and 1')
    const ev = get(e, 'evidence_ids')
    if (!Array.isArray(ev) || (!ev.length && get(e, 'status') !== 'unverified')) E(where + ": evidence_ids is required (empty only when status is 'unverified')")
    if (!isoOk(get(e, 'reported_at', ''))) E(where + ': reported_at must be an ISO 8601 timestamp')
  }
  const location = (loc, where) => {
    if (!isDict(loc) || !LOC_KINDS.has(hashable(loc.kind))) { E(`${where}: location.kind must be one of ${sorted(LOC_KINDS)}`); return }
    const k = loc.kind
    if (k === 'room' && !inSet(rooms, get(loc, 'room_id'))) E(`${where}: room_id ${repr(get(loc, 'room_id'))} does not exist in the plan`)
    if (k === 'anchor') {
      const a = truthy(get(loc, 'anchor')) ? get(loc, 'anchor') : {}
      if (!inSet(rooms, get(a, 'room_id'))) E(`${where}: anchor.room_id ${repr(get(a, 'room_id'))} does not exist in the plan`)
      const pos = get(a, 'position')
      if (!ANCHOR_POS.has(hashable(pos))) E(`${where}: anchor.position must be one of ${sorted(ANCHOR_POS)}`)
      else if (hashable(pos) in NEAR_NEEDS_REF) {
        const pool = NEAR_NEEDS_REF[pos] === 'openings' ? openings : stairs
        if (!inSet(pool, get(a, 'ref_id'))) E(`${where}: anchor.ref_id ${repr(get(a, 'ref_id'))} does not exist (${NEAR_NEEDS_REF[pos]})`)
      }
    }
    if (k === 'floor') {
      if (!inSet(floors, get(loc, 'floor_id')) && !isInt(get(loc, 'floor_reported'))) E(`${where}: floor location needs a valid floor_id or an integer floor_reported`)
    }
    if (k === 'unknown' && (truthy(get(loc, 'room_id')) || truthy(get(loc, 'anchor')))) E(where + ": kind 'unknown' must not carry a room or anchor")
    if ('floor_id' in loc && !inSet(floors, loc.floor_id)) E(`${where}: floor_id ${repr(loc.floor_id)} does not exist in the plan`)
  }

  iter(get(ov, 'occupants', [])).forEach((o, i) => {
    const w = `occupants[${i}]`
    common(o, w)
    location(get(o, 'location'), w)
    const n = get(o, 'count'), r = get(o, 'count_range')
    if (!((isInt(n) && num(n) >= 1) || (Array.isArray(r) && r.length === 2 && r.every(isInt) && 1 <= num(r[0]) && num(r[0]) <= num(r[1])))) E(w + ': needs count (int >= 1) or count_range [min, max]')
  })
  iter(get(ov, 'fire', [])).forEach((f, i) => {
    const w = `fire[${i}]`
    common(f, w)
    location(get(f, 'location'), w)
    if (!FIRE_KINDS.has(hashable(get(f, 'kind')))) E(`${w}: kind must be one of ${sorted(FIRE_KINDS)}`)
  })
  iter(get(ov, 'hazards', [])).forEach((h, i) => {
    const w = `hazards[${i}]`
    common(h, w)
    location(get(h, 'location'), w)
    if (!HAZARD_KINDS.has(hashable(get(h, 'kind')))) E(`${w}: kind must be one of ${sorted(HAZARD_KINDS)}`)
  })
  iter(get(ov, 'blocked', [])).forEach((b, i) => {
    const w = `blocked[${i}]`
    common(b, w)
    const ref = get(b, 'ref_id')
    if (!(openings.has(hashable(ref)) || stairs.has(hashable(ref)) || rooms.has(hashable(ref)))) E(`${w}: ref_id ${repr(ref)} does not exist in the plan`)
  })

  const entities = new Map()
  for (const k of ['occupants', 'fire', 'hazards', 'blocked']) for (const e of iter(get(ov, k, []))) if (truthy(get(e, 'id'))) entities.set(hashable(e.id), e)
  const groups = new Map()
  for (const e of entities.values()) if (truthy(get(e, 'group_id'))) { if (!groups.has(hashable(e.group_id))) groups.set(e.group_id, []); groups.get(e.group_id).push(e) }
  for (const [g, members] of groups) {
    if (members.length < 2) E(`group ${repr(g)} has fewer than 2 members`)
    if (members.some((m) => get(m, 'status') !== 'contradicted')) E(`group ${repr(g)}: every member must have status 'contradicted'`)
  }
  const contradictions = iter(get(ov, 'contradictions', []))
  contradictions.forEach((c, i) => { const g = get(c, 'group_id'); if (!groups.has(hashable(g))) E(`contradictions[${i}]: group_id ${repr(g)} matches no entities`) })
  const conGroups = new Set(contradictions.map((c) => hashable(get(c, 'group_id'))))
  for (const g of groups.keys()) if (!conGroups.has(hashable(g))) E(`group ${repr(g)} has no entry in contradictions[]`)

  const objectives = iter(get(ov, 'objectives', []))
  objectives.forEach((o, i) => {
    const w = `objectives[${i}]`
    reg(get(o, 'id'), w)
    if (!OBJ_TYPES.has(hashable(get(o, 'type')))) E(`${w}: type must be one of ${sorted(OBJ_TYPES)}`)
    const t = truthy(get(o, 'target')) ? get(o, 'target') : {}
    if (!TARGET_KINDS.has(hashable(get(t, 'kind')))) E(`${w}: target.kind must be one of ${sorted(TARGET_KINDS)}`)
    else {
      const ref = get(t, 'ref_id')
      // Python: ref in entities and ref in {e["id"] for e in occupants} (the set is only built when the first test passes)
      const occIds = () => new Set(iter(get(ov, 'occupants', [])).map((e) => { if (!isDict(e) || !('id' in e)) throw new Error("KeyError: 'id'"); return hashable(e.id) }))
      const ok = { occupant: entities.has(hashable(ref)) && occIds().has(ref), occupant_group: groups.has(hashable(ref)), room: rooms.has(hashable(ref)), floor: floors.has(hashable(ref)) || isInt(get(t, 'floor_reported')) }[t.kind]
      if (!ok) E(`${w}: target.ref_id ${repr(ref)} does not resolve for kind ${repr(t.kind)}`)
    }
    if (!isInt(get(o, 'priority')) || num(o.priority) < 1) E(w + ': priority must be an integer >= 1')
    if (!truthy(get(o, 'evidence_ids'))) E(w + ': evidence_ids is required')
  })

  iter(get(ov, 'considerations', [])).forEach((c, i) => {
    const w = `considerations[${i}]`
    reg(get(c, 'id'), w)
    const txt = pyStr(get(c, 'text', ''))
    if (!txt.startsWith('Consider')) E(w + ": text must be advisory and start with 'Consider'")
    if (DIRECTIVE.test(txt) || DIRECTIVE.test(pyStr(get(c, 'rationale', '')))) E(w + ': directive wording found (must, immediately, 100%, ordered, guaranteed are not allowed)')
    if (!truthy(get(c, 'rationale')) || !truthy(get(c, 'evidence_ids'))) E(w + ': rationale and evidence_ids are required')
    for (const r of iter(get(c, 'refs', []))) if (!ids.has(hashable(r)) && !groups.has(hashable(r))) E(`${w}: ref ${repr(r)} matches nothing in this overlay`)
  })

  const objIds = new Set(objectives.map((o) => hashable(get(o, 'id'))))
  iter(get(ov, 'routes', [])).forEach((r, i) => {
    const w = `routes[${i}]`
    reg(get(r, 'id'), w)
    if (get(r, 'produced_by') !== 'planner') E(w + ": routes are produced by the deterministic planner, produced_by must be 'planner'")
    if (!objIds.has(hashable(get(r, 'objective_id')))) E(`${w}: objective_id ${repr(get(r, 'objective_id'))} does not exist`)
    const pools = { room: rooms, opening: openings, stairs, exterior: openings }
    const wps = iter(get(r, 'waypoints', []))
    wps.forEach((wp, j) => {
      if (!WAYPOINT_KINDS.has(hashable(get(wp, 'kind')))) E(`${w}.waypoints[${j}]: kind must be one of ${sorted(WAYPOINT_KINDS)}`)
      else if (!pools[wp.kind].has(hashable(get(wp, 'ref_id')))) E(`${w}.waypoints[${j}]: ref_id ${repr(get(wp, 'ref_id'))} does not exist`)
    })
    if (wps.length < 2) E(w + ': at least 2 waypoints')
  })
}
