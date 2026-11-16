#!/usr/bin/env python3
"""What the SatMap bot says: every command's answer, worked out from SatMap's own pages and nothing else.

No Discord in here (bot.py does that), so each answer can be tried from a terminal against a running server:

    python3 satmap_data.py status --url http://your-server:8765
    python3 satmap_data.py stock "Crushed Iron"
    python3 satmap_data.py nodes Coal

SatMap is only read (GET); nothing is ever sent to the game.  Positions are the game's own centimetres, east = +x,
north = -y; a place is named after the floor of the plan it stands on (plan.json on the map's site), else by its
distance and direction from the HUB.
"""
import json, math, sys, time, urllib.error, urllib.parse, urllib.request

GOOD, BRAND, WARN, BAD, QUIET = 0x5CD682, 0xFA9549, 0xFABE46, 0xF05C54, 0xAABAC8
COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
STATE = {0: 'idle', 1: 'producing', 2: 'paused', 3: 'no power', -1: 'unknown'}
NOT_MACHINES = {'The HUB', 'HUB Terminal', 'MAM', 'Crafting Bench', 'Equipment Workshop', 'Personal Storage Box', 'Space Elevator', 'AWESOME Shop'}


class Unreachable(Exception):
    pass


class SatMap:
    """SatMap's pages, each kept for a few seconds so a burst of commands asks the game's server once."""

    def __init__(self, url, keep=8.0, timeout=6.0):
        self.url, self.keep, self.timeout, self._kept = url.rstrip('/'), keep, timeout, {}

    def get(self, page, keep=None):
        now = time.time()
        hit = self._kept.get(page)
        if hit and now - hit[0] < (self.keep if keep is None else keep):
            return hit[1]
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + '/' + page, headers={'User-Agent': 'satmap-bot'}), timeout=self.timeout) as answer:
                data = json.loads(answer.read().decode('utf-8-sig'))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                data = None           # this SatMap has no such page (an older build, or no site file)
            else:
                raise Unreachable('SatMap answered %d for /%s' % (error.code, page))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise Unreachable(str(getattr(error, 'reason', error)))
        self._kept[page] = (now, data)
        return data

    # -- places ---------------------------------------------------------------------------------------------------
    def hub(self):
        for marker in (self.get('state') or {}).get('markers') or []:
            if marker and marker[0] == 'Hub':
                return marker[2], marker[3]
        return None

    def floors(self):
        plan = self.get('plan.json', keep=300) or {}
        return [b for b in plan.get('boxes') or [] if b.get('k') == 'floor' and b.get('n') and not b.get('yaw')]

    def place(self, x, y):
        """'HUB plaza', or '240 m NE of the HUB', or the bare coordinates in metres."""
        if x is None or y is None:
            return 'far away'
        for floor in self.floors():
            if floor['x'] <= x <= floor['x'] + floor['w'] and floor['y'] <= y <= floor['y'] + floor['d']:
                return floor['n']
        hub = self.hub()
        if not hub:
            return '%d, %d' % (round(x / 100), round(y / 100))
        dx, dy = x - hub[0], y - hub[1]
        metres = math.hypot(dx, dy) / 100
        if metres < 25:
            return 'at the HUB'
        return '%s %s of the HUB' % (dist(metres), COMPASS[round(math.degrees(math.atan2(dx, -dy)) % 360 / 45) % 8])

    def from_hub(self, x, y):
        hub = self.hub()
        return math.hypot(x - hub[0], y - hub[1]) / 100 if hub else math.hypot(x, y) / 100


# ---- small helpers ---------------------------------------------------------------------------------------------
def num(value):
    value = float(value)
    if abs(value) >= 100 or value == int(value):
        return f'{round(value):,}'
    return f'{value:,.1f}'

def dist(metres):
    return '%s km' % num(round(metres / 100) / 10) if metres >= 1000 else '%d m' % (round(metres / 5) * 5 if metres >= 50 else round(metres))

def ago(seconds):
    seconds = max(0, int(seconds))
    days, hours, minutes = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
    return '%d d %d h' % (days, hours) if days else '%d h %d min' % (hours, minutes) if hours else '%d min' % max(minutes, 1) if seconds >= 60 else 'just now'

def bar(part, whole, width=12):
    filled = 0 if whole <= 0 else max(0, min(width, round(width * part / whole)))
    return '▰' * filled + '▱' * (width - filled)

def plural(count, one, many=None):
    return '%s %s' % (num(count), one if count == 1 else (many or one + 's'))

def pick(names, wanted):
    """the name the player meant: exact, then a name that starts with it, then one that holds it; '' for none, a list for several"""
    wanted = (wanted or '').strip().lower()
    if not wanted:
        return ''
    exact = [n for n in names if n.lower() == wanted]
    if exact:
        return exact[0]
    starts = sorted({n for n in names if n.lower().startswith(wanted)})
    holds = sorted({n for n in names if wanted in n.lower()})
    found = starts or holds
    return found[0] if len(found) == 1 else found

def card(title, lines=(), colour=BRAND, badge='digest', fields=(), footer=''):
    return {'title': title, 'lines': [l for l in lines if l], 'colour': colour, 'badge': badge, 'fields': [f for f in fields if f and f[1]], 'footer': footer}

def unsure(what, wanted, found, names):
    if isinstance(found, list) and found:
        return card('Which %s?' % what, ['"%s" fits several:' % wanted, ', '.join(found[:20]) + (' …' if len(found) > 20 else '')], QUIET, 'todo')
    return card('No %s like "%s"' % (what, wanted), ['Known: ' + ', '.join(sorted(names)[:30]) + (' …' if len(names) > 30 else '')] if names else ['Nothing is on record yet.'], QUIET, 'todo')

def stamp(sm):
    state = sm.get('state') or {}
    return '%s · SatMap %s' % (state.get('session') or 'no world', state.get('modVersion') or '?')


# ---- the commands ----------------------------------------------------------------------------------------------
# One look for every answer: a short headline (the title), one sentence that says how things stand, then either a
# few boxes side by side (fields) or one aligned table (a code block).  Dots carry state at a glance:
# green running, yellow waiting, orange a warning, red a fault, white unknown.
DOT = {'producing': '🟢', 'idle': '🟡', 'paused': '⏸️', 'no power': '🔴', 'unknown': '⚪'}
SEV = {3: '🔴', 2: '🟠', 1: '🟡'}

def when(unix):
    """Discord shows this as "5 minutes ago" and keeps it current"""
    return '<t:%d:R>' % int(unix)

def table(rows, head=None):
    """rows of cells as one aligned block; numbers right, the last column left"""
    rows = [[str(c) for c in r] for r in rows]
    if not rows:
        return ''
    cols = len(rows[0])
    wide = [max(len(r[i]) for r in rows + ([head] if head else [])) for i in range(cols)]
    line = lambda r: '  '.join(r[i].rjust(wide[i]) if i < cols - 1 else r[i] for i in range(cols)).rstrip()
    body = ([line(head), '  '.join('─' * w for w in wide[:-1]) + '  ' + '─' * min(wide[-1], 24)] if head else []) + [line(r) for r in rows]
    return '```\n' + '\n'.join(body) + '\n```'

def _players(state):
    return [p for p in state.get('players') or (state.get('server') or {}).get('players') or [] if isinstance(p, dict) and p.get('name')]

def _machines(sm):
    """the world's machines without storages, the HUB and the benches: [classIndex, x, y, z, yaw, recipe, clock, state, productivity]"""
    w, s = sm.get('world') or {}, sm.get('stock') or {}
    names, world_classes, classes = w.get('names') or [], w.get('classes') or [], s.get('classes') or []
    stores = {classes[c[0]] for c in s.get('containers') or [] if c[0] < len(classes)}          # class names
    return [m for m in w.get('machines') or [] if m[0] < len(names) and names[m[0]] not in NOT_MACHINES
            and (world_classes[m[0]] if m[0] < len(world_classes) else '') not in stores]

def status(sm, arg=None):
    state = sm.get('state')
    server, power, alerts = state.get('server') or {}, state.get('power') or {}, (state.get('alerts') or {}).get('list') or []
    players = [p['name'] for p in _players(state)]
    faults = sum(1 for a in alerts if a.get('sev', 0) >= 3)
    elevator = state.get('elevator') or {}
    need = elevator.get('items') or []
    rows = _machines(sm)
    running = sum(1 for m in rows if m[7] == 1)
    if power.get('fuse'):
        headline, colour = '🔴 A fuse has blown: the power is off.', BAD
    elif faults:
        headline, colour = '🔴 %s to deal with.' % plural(faults, 'fault'), BAD
    elif alerts:
        headline, colour = '🟠 %s attention.' % ('1 thing needs' if len(alerts) == 1 else '%d things need' % len(alerts)), WARN
    else:
        headline, colour = '🟢 Everything the map watches is running.', GOOD
    fields = [
        ('Online', '\n'.join(players[:8]) if players else 'nobody', True),
        ('Power', '**%s** of %s MW\n%s' % (num(power.get('consumed', 0)), num(power.get('capacity', 0)), bar(power.get('consumed', 0), power.get('capacity', 0), 10)), True),
        ('Alerts', 'none' if not alerts else '**%d** open\n%s' % (len(alerts), '  '.join(x for x in ('🔴 %d' % faults if faults else '', '🟠 %d' % (len(alerts) - faults) if len(alerts) > faults else '') if x)), True),
        ('Phase', state.get('phaseName') or '?', True),
        ('Factory', '%s\n%d of %d machines on' % (plural(server.get('buildables', 0), 'building'), running, len(rows)), True),
        ('Server', 'up %s\nsaved %s' % (ago(server.get('upSeconds', 0)), when(server['lastSave']) if server.get('lastSave') else '?'), True),
    ]
    if need:
        fields.append(('Space Elevator · %s' % (elevator.get('phase') or 'next delivery'), '\n'.join('%s  %s %s/%s' % (bar(i['paid'], i['need'], 8), i['n'], num(i['paid']), num(i['need'])) for i in need[:6]), False))
    return card(state.get('session') or 'The server', [headline], colour, 'server', fields, stamp(sm))

def online(sm, arg=None):
    state = sm.get('state')
    players = _players(state)
    if not players:
        return card('Nobody is online', ['The server has been up %s and is waiting for pioneers.' % ago((state.get('server') or {}).get('upSeconds', 0))], QUIET, 'online', footer=stamp(sm))
    return card(plural(len(players), 'pioneer') + ' online', ['🟢 **%s**  ·  %s' % (p['name'], sm.place(p.get('x'), p.get('y'))) for p in players], GOOD, 'online', footer=stamp(sm))

def power(sm, arg=None):
    p = (sm.get('state') or {}).get('power') or {}
    grids = p.get('grids') or []
    used, cap = p.get('consumed', 0), p.get('capacity', 0)
    if p.get('fuse'):
        headline = '🔴 **A fuse has blown: the power is off.** Reset it at a power pole or a switch.'
    elif not cap:
        headline = '⚪ No generator is running.'
    elif used > 0.9 * cap:
        headline = '🟠 Running close to the limit: %s MW of headroom.' % num(cap - used)
    else:
        headline = '🟢 %s MW of headroom.' % num(cap - used)
    lines = [headline, '%s  **%s** of **%s MW**' % (bar(used, cap, 16), num(used), num(cap)) if cap else '']
    fields = []
    for g in grids[:9]:
        battery = '' if not g.get('battCap') else '\n🔋 %s of %s MWh%s' % (num(g.get('batt', 0)), num(g['battCap']), '' if g.get('battMin') in (None, 'null') else ' · %s min left' % num(g['battMin']))
        fields.append(('%s %s' % ('🔴' if g.get('fuse') else '🟢', g.get('name', 'Grid')),
                       '**%s** of %s MW\npeak draw %s MW\n%s, %s\n%s%s' % (num(g.get('cons', 0)), num(g.get('cap', 0)), num(g.get('max', 0)),
                           plural(g.get('gens', 0), 'generator'), plural(g.get('users', 0), 'user'), sm.place(g.get('x'), g.get('y')), battery), True))
    return card('Power · %s' % plural(p.get('circuits', 0), 'grid'), lines, BAD if p.get('fuse') else WARN if cap and used > 0.9 * cap else GOOD if cap else QUIET, 'fuse' if p.get('fuse') else 'power', fields, stamp(sm))

def stock(sm, arg=None):
    s, r = sm.get('stock') or {}, sm.get('rates') or {}
    items = s.get('items') or []
    names = [i[1] for i in items]
    if not arg:
        top = sorted((i for i in items if i[2] > 0), key=lambda i: -i[2])
        if not top:
            return card('Storage is empty', ['Nothing is in any container yet.'], QUIET, 'stock', footer=stamp(sm))
        lines = ['%s in %s.' % (plural(len(top), 'kind of item', 'kinds of item').capitalize(), plural(s.get('numContainers', len(s.get('containers') or [])), 'container')),
                 table([[num(i[2]), i[1]] for i in top[:22]]), '… and %d more kinds' % (len(top) - 22) if len(top) > 22 else '']
        return card('In storage', lines, BRAND, 'stock', footer='/stock <item> shows where it is  ·  ' + stamp(sm))
    name = pick(names, arg)
    if not isinstance(name, str) or not name:
        return unsure('item', arg, name, names)
    index = names.index(name)
    holds = sorted(((count, c) for c in s.get('containers') or [] for i, count in c[4] if i == index), key=lambda t: -t[0])
    classes, w = s.get('classes') or [], sm.get('world') or {}
    shown = dict(zip(w.get('classes') or [], w.get('names') or []))                             # class name -> the name a player sees
    fields = [('In storage', '**%s**' % num(items[index][2]), True), ('Held in', plural(len(holds), 'container'), True)]
    rate = next((x for x in r.get('items') or [] if x.get('n') == name), None)
    if rate:
        net = rate['made'] - rate['used']
        fields.append(('A minute', 'made %s · used %s' % (num(rate['made']), num(rate['used'])), True))
        if net < -0.05:
            fields.append(('At this rate', '🟠 runs out in about %s' % ago(items[index][2] / -net * 60), False))
        elif net > 0.05:
            fields.append(('At this rate', '🟢 %s a minute to spare' % num(net), False))
    rows = [[num(count), shown.get(classes[c[0]], 'Container') if c[0] < len(classes) else 'Container', sm.place(c[1], c[2])] for count, c in holds[:12]]
    return card(name, [table(rows, ['Count', 'Container', 'Where'])] if rows else [], BRAND, 'stock', fields, stamp(sm))

def rates(sm, arg=None):
    r = sm.get('rates') or {}
    items = r.get('items') or []
    names = [i['n'] for i in items]
    if not arg:
        rows = sorted(items, key=lambda i: -(i['cap'] or 0))
        if not rows:
            return card('Nothing is being made', ['No machine has a recipe yet.'], QUIET, 'rates', footer=stamp(sm))
        making = sum(1 for i in rows if i['made'] > 0)
        lines = ['%s %d of %d items are being made right now. Figures are items a minute.' % ('🟢' if making == len(rows) else '🟠' if making else '🔴', making, len(rows)),
                 table([[num(i['made']), num(i['used']), num(i['cap']), i['n']] for i in rows[:22]], ['Made', 'Used', 'Flat out', 'Item'])]
        return card('Production', lines, GOOD if making == len(rows) else WARN if making else BAD, 'rates', footer='/rates <item> for the machines behind it  ·  ' + stamp(sm))
    name = pick(names, arg)
    if not isinstance(name, str) or not name:
        return unsure('item', arg, name, names)
    item = items[names.index(name)]
    recipes, classes = r.get('recipes') or [], r.get('classes') or []
    made_by = [m for m in r.get('machines') or [] if 0 <= m[4] < len(recipes) and recipes[m[4]] == name]
    by_kind = {}
    for m in made_by:
        by_kind.setdefault(classes[m[3]] if m[3] < len(classes) else 'Machine', []).append(m)
    share = 0 if not item['cap'] else item['made'] / item['cap']
    lines = ['%s  **%s %%** of what the machines could make' % (bar(item['made'], item['cap'], 16), num(share * 100))] if item['cap'] else []
    fields = [('Made a minute', '**%s**' % num(item['made']), True), ('Used a minute', '**%s**' % num(item['used']), True), ('Flat out', '%s' % num(item['cap']), True)]
    if by_kind:
        fields.append(('Machines on this recipe', '\n'.join('%d × %s, at %s %% on average' % (len(ms), kind, num(sum(max(m[5], 0) for m in ms) / len(ms))) for kind, ms in sorted(by_kind.items())), False))
    return card(name, lines, GOOD if share >= 0.9 else WARN if share > 0 else BAD if item['cap'] else QUIET, 'rates', fields, stamp(sm))

def machines(sm, arg=None):
    w = sm.get('world') or {}
    names = w.get('names') or []
    rows = _machines(sm)
    kinds = {}
    for m in rows:
        kinds.setdefault(names[m[0]] if m[0] < len(names) else 'Machine', []).append(m)
    if arg:
        name = pick(list(kinds), arg)
        if not isinstance(name, str) or not name:
            return unsure('machine', arg, name, list(kinds))
        ms = kinds[name]
        lines = ['%s %s  ·  %s%s' % (DOT.get(STATE.get(m[7], 'unknown'), '⚪'), sm.place(m[1], m[2]), STATE.get(m[7], 'unknown'), '' if len(m) < 9 or m[8] is None or m[8] < 0 or m[7] != 1 else ' at %s %%' % num(m[8])) for m in ms[:18]]
        running = sum(1 for m in ms if m[7] == 1)
        return card('%d × %s' % (len(ms), name), lines + (['… and %d more' % (len(ms) - 18)] if len(ms) > 18 else []), GOOD if running == len(ms) else WARN if running else BAD, 'machines',
                    footer='%d of %d running  ·  %s' % (running, len(ms), stamp(sm)))
    if not rows:
        return card('No machines yet', ['Nothing that makes, mines or burns is built yet.'], QUIET, 'machines', footer=stamp(sm))
    total = {}
    for m in rows:
        total[STATE.get(m[7], 'unknown')] = total.get(STATE.get(m[7], 'unknown'), 0) + 1
    lines = ['  ·  '.join('%s %d %s' % (DOT.get(state, '⚪'), n, state) for state, n in sorted(total.items(), key=lambda kv: -kv[1]))]
    for name, ms in sorted(kinds.items(), key=lambda kv: -len(kv[1]))[:24]:
        count = {}
        for m in ms:
            count[STATE.get(m[7], 'unknown')] = count.get(STATE.get(m[7], 'unknown'), 0) + 1
        lines.append('**%d × %s**  %s' % (len(ms), name, '  '.join('%s %d' % (DOT.get(state, '⚪'), n) for state, n in sorted(count.items(), key=lambda kv: -kv[1]))))
    running = total.get('producing', 0)
    return card(plural(len(rows), 'machine'), lines, GOOD if running == len(rows) else WARN if running else BAD, 'machines', footer='/machines <name> lists them one by one  ·  ' + stamp(sm))

def logistics(sm, arg=None):
    w = sm.get('world') or {}
    part = set(w.get('part') or [])
    runs = {}                                    # (kind, tier) -> [runs, metres], along the run's own course
    for path in w.get('paths') or []:
        kind, index, mark, course = path[0], path[1], path[2], path[3]
        if index in part or (len(path) > 4 and path[4] == 'part'):
            continue                             # the short pieces inside a machine, which nobody built by hand
        points = [course[i:i + 3] for i in range(0, len(course) - 2, 3)]
        entry = runs.setdefault((kind, mark), [0, 0.0]); entry[0] += 1; entry[1] += sum(math.dist(a, b) for a, b in zip(points, points[1:])) / 100
    # a machine's own stubs come as belts of exactly one metre in a tier the world has not unlocked
    for key in [key for key, v in runs.items() if key[0] == 'belt' and v[1] / max(v[0], 1) <= 1.05]:
        del runs[key]
    wires = w.get('wires') or []
    counts = w.get('counts') or {}
    def built(*words):
        return sum(n for c, n in counts.items() if any(word in c for word in words)) if isinstance(counts, dict) else 0
    def by_tier(kind):
        rows = sorted((mark, v) for (k, mark), v in runs.items() if k == kind)
        return '\n'.join('Mk.%s  **%s**\n-# %s' % (mark, dist(v[1]), plural(v[0], 'run')) for mark, v in rows)
    length = lambda kind: sum(v[1] for (k, _), v in runs.items() if k == kind)
    fields = [('Belts', by_tier('belt') or 'none', True), ('Pipes', by_tier('pipe') or 'none', True),
              ('Power lines', '**%s**\n-# %s' % (dist(sum(math.hypot(a[2] - a[0], a[3] - a[1]) for a in wires) / 100), plural(len(wires), 'line')) if wires else 'none', True)]
    for kind in sorted({k for k, _ in runs} - {'belt', 'pipe'}):
        fields.append((kind.capitalize() + 's', by_tier(kind), True))
    fields.append(('On the belts', '%s  ·  %s  ·  %s' % (plural(built('AttachmentSplitter', 'SplitterSmart', 'SplitterProgrammable'), 'splitter'), plural(built('AttachmentMerger', 'MergerPriority'), 'merger'),
                                                        plural(built('ConveyorPole', 'PipelineSupport'), 'pole and support', 'poles and supports')), False))
    return card('Logistics', ['**%s** of belt and **%s** of pipe carry the factory.' % (dist(length('belt')), dist(length('pipe')))], BRAND, 'logistics', fields, 'Lengths follow each run\'s own course  ·  ' + stamp(sm))

def nodes(sm, arg=None):
    rows = (sm.get('nodes') or {}).get('nodes') or []
    names = sorted({r[2] for r in rows if r[2]})
    if not arg:
        by = {}
        for r in rows:
            entry = by.setdefault(r[2] or r[7], [0, 0]); entry[0] += 1; entry[1] += 1 if r[8] else 0
        ranked = sorted(by.items(), key=lambda kv: -kv[1][0])
        lines = ['%s on the map, %d with an extractor on it.' % (plural(len(rows), 'resource node').capitalize(), sum(v[1] for v in by.values())),
                 table([[v[0], v[1] or '·', name] for name, v in ranked[:26]], ['Nodes', 'In use', 'Resource'])]
        return card('Resources', lines, BRAND, 'nodes', footer='/nodes <resource> for the nearest free ones  ·  ' + stamp(sm))
    name = pick(names, arg)
    if not isinstance(name, str) or not name:
        return unsure('resource', arg, name, names)
    mine = [r for r in rows if r[2] == name]
    free = sorted((r for r in mine if not r[8]), key=lambda r: sm.from_hub(r[4], r[5]))
    purity = {}
    for r in mine:
        purity[r[3] or 'unrated'] = purity.get(r[3] or 'unrated', 0) + 1
    fields = [('On the map', '**%d**' % len(mine), True), ('In use', '%d' % sum(1 for r in mine if r[8]), True),
              ('Purity', '\n'.join('%d %s' % (purity[p], p) for p in ('pure', 'normal', 'impure', 'unrated') if p in purity), True)]
    lines = [table([[(r[3] or '·'), sm.place(r[4], r[5]) + ('' if r[7] == 'node' else ' (%s)' % r[7])] for r in free[:10]], ['Purity', 'Nearest free']) if free else '🟠 Every one already has an extractor.']
    return card(name, lines, BRAND, 'nodes', fields, stamp(sm))

def alerts(sm, arg=None):
    rows = sorted(((sm.get('state') or {}).get('alerts') or {}).get('list') or [], key=lambda a: (-a.get('sev', 0), a.get('since', 0)))
    if not rows:
        return card('All clear', ['🟢 Everything the map watches is running.'], GOOD, 'cleared', footer=stamp(sm))
    lines = ['%s **%s**%s\n-# %s  ·  %s' % (SEV.get(a.get('sev'), '⚪'), a.get('text') or a.get('n'), ' (%s)' % a['more'] if a.get('more') else '', sm.place(a.get('x'), a.get('y')),
                                               when(a['since']) if a.get('since') else '') for a in rows[:10]]
    worst = rows[0].get('sev', 1)
    return card(plural(len(rows), 'open alert'), lines + (['… and %d more' % (len(rows) - 10)] if len(rows) > 10 else []), BAD if worst >= 3 else WARN, 'alerts', footer='Worst first  ·  ' + stamp(sm))

def elevator(sm, arg=None):
    state = sm.get('state') or {}
    e = state.get('elevator') or {}
    items = e.get('items') or []
    lines = ['%s  **%s**  %s of %s' % (bar(i['paid'], i['need'], 14), i['n'], num(i['paid']), num(i['need'])) for i in items]
    done = items and all(i['paid'] >= i['need'] for i in items)
    return card('Space Elevator · %s' % (e.get('phase') or 'not built yet'), lines or ['Nothing is being asked for.'], GOOD if done else BRAND, 'phase',
                [('Game phase', '%s' % (state.get('phaseName') or '?'), True), ('Milestones and research', '%s bought' % num(len(state.get('purchased') or [])), True)], stamp(sm))

def digest(sm, arg=None):
    d = sm.get('digest') or {}
    built = [b for b in d.get('built') or [] if b.get('n') and b['n'] != 'Health']
    removed = [b for b in d.get('removed') or [] if isinstance(b, dict) and b.get('n') and b['n'] != 'Health'] if isinstance(d.get('removed'), list) else []
    fields = []
    if built:
        fields.append(('Built', table([[b['count'], b['n']] for b in built[:14]]) + ('… and %d more kinds' % (len(built) - 14) if len(built) > 14 else ''), True))
    if removed:
        fields.append(('Taken down', table([[b['count'], b['n']] for b in removed[:14]]), True))
    for label, key in (('Bought', 'bought'), ('Phases', 'phases')):
        rows = d.get(key) or []
        if rows:
            fields.append((label, ', '.join((x.get('n') or str(x)) if isinstance(x, dict) else str(x) for x in rows[:12]), False))
    who = sorted({b['who'] for b in built if b.get('who')})
    if who:
        fields.append(('Builders', ' · '.join(who), False))
    total = sum(b['count'] for b in built)
    return card('The record · last %s' % ago(d.get('away', 0)), ['**%s** built and **%s** taken down.' % (plural(total, 'piece'), plural(sum(b['count'] for b in removed), 'piece'))], BRAND, 'digest', fields, stamp(sm))

def harddrives(sm, arg=None):
    pods = (sm.get('state') or {}).get('dropPods') or {}
    left = sorted(pods.get('left') or [], key=lambda p: sm.from_hub(p[1], p[2]))
    known = pods.get('known', len(left))
    lines = ['%s  **%s** of %s crash sites opened' % (bar(known - len(left), known, 14), num(known - len(left)), num(known)),
             table([[i + 1, sm.place(p[1], p[2])] for i, p in enumerate(left[:10])], ['#', 'Nearest unopened']) if left else '🟢 Every crash site has been opened.']
    return card('Hard drives', lines, BRAND, 'harddrive', footer=stamp(sm))

def todo(sm, arg=None):
    board = sm.get('board') or {}
    rows = board.get('todo') or []
    text = lambda r: r.get('text') or r.get('n') or str(r) if isinstance(r, dict) else (r[-1] if isinstance(r, list) and r else str(r))
    pins = board.get('pins') or []
    check = sm.get('plancheck') or {}
    lines = ['▫️ ' + text(r) for r in rows[:15]] or ['The shared list is empty. Add to it on the map\'s page or in the game.']
    fields = [('Pins on the map', num(len(pins)), True)] if pins else []
    if check.get('plan'):
        fields.append(('Plan check', str(check.get('note') or check.get('plan'))[:200], False))
    return card('To do · %s' % plural(len(rows), 'note'), lines, BRAND, 'todo', fields, stamp(sm))

def helptext(sm=None, arg=None):
    lines = ['`/%s`%s  %s' % (name, ' `%s`' % argument if argument else '', SHORT.get(name, about)) for name, (_, about, argument) in COMMANDS.items() if name != 'help']
    return card('SatMap · commands', lines + ['-# Names are suggested as you type. Leave the name out for the overview.'], BRAND, 'help', footer='Read live from the server; nothing is ever changed in the game.')

# name: (function, what it says in Discord, the name of its optional argument or None)
COMMANDS = {
    'status': (status, 'The server at a glance: who is on, power, alerts, the phase', None),
    'online': (online, 'Who is playing, and where they are', None),
    'power': (power, 'Each power grid: use, capacity, batteries, fuses', None),
    'stock': (stock, 'What is in storage; name an item to see where it is and how long it lasts', 'item'),
    'rates': (rates, 'Items made and used a minute; name an item for the machines behind it', 'item'),
    'machines': (machines, 'Every kind of machine and how many are running; name one to list them', 'machine'),
    'logistics': (logistics, 'Belts, pipes and power lines by tier and length, splitters, mergers, lifts', None),
    'nodes': (nodes, 'Resource nodes; name a resource for the nearest free ones', 'resource'),
    'alerts': (alerts, 'Everything that needs attention, worst first', None),
    'elevator': (elevator, 'What the Space Elevator still needs', None),
    'digest': (digest, 'What has been built, taken down and bought', None),
    'harddrives': (harddrives, 'Crash sites that still hold a hard drive, nearest first', None),
    'todo': (todo, 'The shared to-do list and pins', None),
    'help': (helptext, 'This list', None),
}

# the one-line form for /help
SHORT = {'status': 'The server at a glance', 'online': 'Who is playing, and where', 'power': 'Every power grid', 'stock': 'What is in storage, and where', 'rates': 'What is made and used a minute',
         'machines': 'What runs and what stands still', 'logistics': 'Belts, pipes and power lines', 'nodes': 'Resources and the nearest free nodes', 'alerts': 'What needs attention',
         'elevator': 'What the Space Elevator needs', 'digest': 'What was built and bought', 'harddrives': 'Crash sites still unopened', 'todo': 'The shared to-do list'}

def choices(sm, command):
    """the names a command's argument can take, for Discord's suggestions"""
    try:
        if command in ('stock', 'rates'):
            return sorted({i[1] for i in (sm.get('stock') or {}).get('items') or []} | {i['n'] for i in (sm.get('rates') or {}).get('items') or []})
        if command == 'machines':
            names = (sm.get('world') or {}).get('names') or []
            return sorted({names[m[0]] for m in _machines(sm)})
        if command == 'nodes':
            return sorted({r[2] for r in (sm.get('nodes', keep=600) or {}).get('nodes') or [] if r[2]})
    except Unreachable:
        pass
    return []

def answer(sm, command, arg=None):
    """the card for a command; never raises"""
    try:
        return COMMANDS[command][0](sm, arg)
    except Unreachable as error:
        return card('The map\'s server is not answering', ['SatMap at %s could not be reached (%s). Is the game server up?' % (sm.url, error)], QUIET, 'leave')
    except Exception as error:                                   # a page in a shape this bot does not know
        return card('That did not work', ['`/%s` tripped over the data it got: %s: %s' % (command, type(error).__name__, error)], QUIET, 'todo')

def presence(sm):
    """one line for the bot's status under its name"""
    try:
        state = sm.get('state', keep=30)
    except Unreachable:
        return 'the server is down'
    players = _players(state)
    power, alerts = state.get('power') or {}, (state.get('alerts') or {}).get('list') or []
    return ' · '.join(x for x in ('%d online' % len(players), '%s MW' % num(power.get('consumed', 0)), plural(len(alerts), 'alert') if alerts else 'all running') if x)

def as_text(c):
    out = ['== %s  [%s, #%06x]' % (c['title'], c['badge'], c['colour'])] + c['lines'] + ['-- %s: %s' % (f[0], f[1].replace('\n', ' | ')) for f in c['fields']]
    return '\n'.join(out + (['(%s)' % c['footer']] if c['footer'] else []))

if __name__ == '__main__':
    args = sys.argv[1:]
    url = 'http://127.0.0.1:8765'
    if '--url' in args:
        url = args[args.index('--url') + 1]; del args[args.index('--url'):args.index('--url') + 2]
    if not args or args[0] not in COMMANDS and args[0] != 'presence':
        sys.exit('usage: satmap_data.py <%s|presence> [name] [--url http://host:8765]' % '|'.join(COMMANDS))
    sm = SatMap(url)
    print(presence(sm) if args[0] == 'presence' else as_text(answer(sm, args[0], ' '.join(args[1:]) or None)))
