"""Background feeds that put the live Minecraft world into Echo.

* WorldStreamFeed: hidden Minecraft -> immutable section packets the Echo plugin
  draws (LOCALAPPDATA/EchoCraft/world-stream, see world_stream.py).
* EchoHeadFeed: Echo's local session API -> echo-head.bin, the reference the
  plugin's anchor uses to find Echo's tracking->world transform.
* BodyFeed: Echo head -> Minecraft player position (/body), so Minecraft loads
  chunks, simulates and validates reach where the player actually is in Echo.
Echo owns locomotion; Minecraft follows. Each feed is a daemon thread that
survives transient errors and reports its own status.
"""
import json
import math
import os
from pathlib import Path
import struct
import threading
import time
import urllib.error
import urllib.request
from bridge_cli import Bridge, DEFAULT_CONFIG, ROOT
from scene_geometry import Alignment
from world_stream import WorldStream

LOCAL = Path(os.environ.get('LOCALAPPDATA', str(ROOT/'runtime')))/'EchoCraft'
HEAD_FILE = LOCAL/'echo-head.bin'
STREAM_DIR = LOCAL/'world-stream'
HEAD_MAGIC = 0x48484345
HANDS_FILE = LOCAL/'echo-hands.bin'  # local avatar hands: the plugin measures avatar hand -> controller from them
HANDS_MAGIC = 0x4e484345


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp'); temp.write_bytes(data)
    for attempt in range(40):
        try: os.replace(temp, path); return
        except PermissionError:
            if attempt == 39: raise
            time.sleep(.005)


FIXED_COORDINATES = LOCAL/'fixed-coordinates.enabled'


def fixed_mapping():
    """Fixed coordinates: one Minecraft->Echo mapping shared by every player. The switch file holds 'ox oy oz [fx fy fz]':
    Minecraft point (ox,oy,oz) sits at the Echo origin and Minecraft direction f points along Echo -Z. The yaw must match
    the level's anchor block (32.4 deg here): live collision cubes are copies of it, and an unrotated mapping twisted
    every cube (session 33). Defaults: origin 0 0 0, f = 0 0 -1."""
    values = [float(v) for v in FIXED_COORDINATES.read_text().split()]
    origin = values[:3] if len(values) >= 3 else [0.0, 0.0, 0.0]
    forward = values[3:6] if len(values) >= 6 else [0.0, 0.0, -1.0]
    if not all(math.isfinite(v) for v in origin + forward): raise ValueError('Invalid fixed-coordinates mapping')
    return origin, forward


def load_alignment():
    # Fixed coordinates: Minecraft is the truth; EchoCraftPhysics moves the Echo body instead of re-anchoring the world.
    if FIXED_COORDINATES.exists():
        origin, forward = fixed_mapping()
        return dict(echo_head=[0.0, 0.0, 0.0], minecraft_head=origin, echo_forward=[0.0, 0.0, -1.0], minecraft_forward=forward)
    world = json.loads((ROOT/'runtime/world-replacement/world-input.json').read_text())
    return world['alignment']


def write_alignment_file(directory, alignment):
    """12 numbers for the native plugin: echo head, Minecraft head, echo forward, Minecraft forward."""
    values = [*alignment['echo_head'], *alignment['minecraft_head'], *alignment['echo_forward'], *alignment['minecraft_forward']]
    if len(values) != 12 or not all(math.isfinite(v) for v in values): raise ValueError('Invalid alignment')
    (Path(directory)/'live-world-alignment.txt').write_text(' '.join(repr(float(v)) for v in values)+'\n')


def local_player(session):
    name = session.get('client_name')
    players = [p for t in session.get('teams', []) for p in t.get('players', []) if name and p.get('name') == name]
    return players[0] if len(players) == 1 else None


def head_packet(seq, player):
    head = player['head']
    values = [*head['position'], *head['left'], *head['up'], *head['forward'], *player.get('velocity', [0, 0, 0])]
    if len(values) != 15 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise ValueError('Malformed head pose')
    return struct.pack('<IIQd15d', HEAD_MAGIC, 1, seq, time.time()*1000, *values)


def hands_packet(seq, player):
    """Local lhand/rhand as the API reports them (position + left/up/forward = the hand rotation's X/Y/Z columns), with the
    player's velocity so the plugin only calibrates while still."""
    values = [v for key in ('lhand', 'rhand') for part in ('pos', 'left', 'up', 'forward') for v in player[key][part]]
    values += player.get('velocity', [0, 0, 0])
    if len(values) != 27 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise ValueError('Malformed hand pose')
    return struct.pack('<IIQd27d', HANDS_MAGIC, 1, seq, time.time()*1000, *values)


def minecraft_look(forward):
    x, y, z = forward; length = math.sqrt(x*x+y*y+z*z) or 1
    x, y, z = x/length, y/length, z/length
    return math.degrees(math.atan2(-x, z)), -math.degrees(math.asin(max(-1, min(1, y))))


class Feed(threading.Thread):
    def __init__(self, name):
        super().__init__(name=name, daemon=True); self.stop = threading.Event(); self.status = 'starting'; self.errors = 0

    def report(self): return dict(status=self.status, errors=self.errors)


class EchoHeadFeed(Feed):
    def __init__(self, port=6721, interval=.05):
        super().__init__('echo-head'); self.port = port; self.interval = interval; self.seq = 0
        self.lock = threading.Lock(); self.latest = None; self.blocking = False  # Echo forearm-shield block

    def head(self, max_age=.5):
        with self.lock:
            if self.latest and time.monotonic()-self.latest[0] <= max_age: return self.latest[1]
        return None

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/session', timeout=1.0) as response:
                    session = json.load(response)
                player = local_player(session) if session.get('err_code', 0) == 0 else None
                if player:
                    self.seq += 1; atomic_bytes(HEAD_FILE, head_packet(self.seq, player))
                    try: atomic_bytes(HANDS_FILE, hands_packet(self.seq, player))
                    except (KeyError, TypeError, ValueError): pass  # hands are optional; the head feed must keep going
                    with self.lock: self.latest = (time.monotonic(), player['head']); self.blocking = bool(player.get('blocking'))
                    self.status = 'publishing'
                else: self.status = 'not-in-a-match'
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError) as error:
                # 404 outside matches; connection refused while loading or with the API disabled.
                self.status = f'unavailable: {error}'[:120]; self.errors += 1
                self.stop.wait(.5)
            self.stop.wait(max(0, self.interval-(time.monotonic()-started)))


class WorldStreamFeed(Feed):
    def __init__(self, horizontal=5, vertical=2, per_tick=4, interval=.05):  # 5 sections = ~80 m (was 4)
        super().__init__('world-stream'); self.args = (horizontal, vertical, per_tick, interval); self.stream = None
        # Smoothed per-tick costs (ms) and edit (re-export of a resident section) latency, for diagnosis.
        self.timing = dict(worldMs=0.0, exportMs=0.0, publishMs=0.0, tickMs=0.0, editExports=0, maxExportMs=0.0)

    def report(self):
        result = super().report()
        if self.stream: result.update(resident=len(self.stream.resident), exports=self.stream.exports,
                                      pending=len(self.stream.pending()) if self.stream.state else None,
                                      timing=dict(self.timing))
        return result

    def run(self):
        horizontal, vertical, per_tick, interval = self.args
        # One stream for the whole session: a transient bridge error must never drop the
        # resident sections (that made the world flash). WorldStream.sync() itself resets
        # only on a real world/dimension/resource change.
        self.stream = stream = WorldStream(STREAM_DIR)
        while not self.stop.is_set():
            try:
                bridge = Bridge()
                while not self.stop.is_set():
                    started = time.monotonic()
                    stream.sync(bridge.request('/world', dict(horizontal=horizontal, vertical=vertical)))
                    synced = time.monotonic(); exported = 0.0
                    if stream.atlas is None:
                        bridge.request('/assets')
                        stream.set_atlas((DEFAULT_CONFIG.parent.parent/'echocraft-export/echocraft_blocks_0.png').read_bytes())
                    # Binary packets (/section_packet) and a time budget instead of a fixed count: as many sections
                    # as fit in ~40 ms, nearest/collision-critical first (WorldStream.pending ordering).
                    budget_end = started + .04
                    for n, key in enumerate(stream.pending()[:per_tick * 4]):
                        if n and time.monotonic() > budget_end: break
                        wanted = stream.wanted[key]; edit = key in stream.resident; t0 = time.monotonic()
                        try:
                            stream.accept_packet(bridge.request('/section_packet', dict(section=list(key), worldSession=stream.identity[0],
                                                                                       dimension=stream.identity[1], revision=wanted['revision'])))
                        except urllib.error.HTTPError as error:
                            if error.code == 400: break  # section/world changed mid-capture; resync next tick
                            raise
                        cost = (time.monotonic()-t0)*1000; exported += cost
                        self.timing['maxExportMs'] = max(self.timing['maxExportMs']*.995, cost)
                        if edit: self.timing['editExports'] += 1
                        if edit and n >= 1: break  # publish edits promptly, but not one section per pass
                    t1 = time.monotonic(); stream.publish(); self.status = 'streaming'
                    for name, value in (('worldMs', (synced-started)*1000), ('exportMs', exported), ('publishMs', (time.monotonic()-t1)*1000), ('tickMs', (time.monotonic()-started)*1000)):
                        self.timing[name] = round(self.timing[name]*.9 + value*.1, 2)
                    self.stop.wait(max(0, interval-(time.monotonic()-started)))
            except Exception as error:  # noqa: BLE001 - keep streaming through world loads/restarts
                self.status = f'retrying: {type(error).__name__}: {error}'[:160]; self.errors += 1
                self.stop.wait(1)


class BodyFeed(Feed):
    """Moves the Minecraft player to Echo's head. Lease-based on the Java side."""
    def __init__(self, heads, alignment, interval=.05):
        super().__init__('body'); self.heads = heads; self.alignment = Alignment(**alignment); self.interval = interval
        self.alignment_stamp = None

    def follow_alignment(self):
        """Dismounting re-anchors the world (the Echo plugin rewrites the live alignment); follow it at once."""
        path = LOCAL/'live-world-alignment.txt'
        try: stamp = path.stat().st_mtime_ns
        except OSError: return
        if stamp == self.alignment_stamp: return
        values = [float(v) for v in path.read_text().split()]
        if len(values) != 12 or not all(math.isfinite(v) for v in values): return
        self.alignment = Alignment(echo_head=values[0:3], minecraft_head=values[3:6], echo_forward=values[6:9], minecraft_forward=values[9:12])
        self.alignment_stamp = stamp

    def run(self):
        bridge = None
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.follow_alignment()
                head = self.heads.head()
                if head is None: self.status = 'waiting-for-echo-head'
                else:
                    bridge = bridge or Bridge()
                    state_session = getattr(self, 'session', None) or bridge.request('/state').get('worldSession')
                    self.session = state_session
                    position = self.alignment.to_minecraft(head['position'])
                    yaw, pitch = minecraft_look(self.alignment.direction(head['forward']))
                    bridge.request('/body', dict(worldSession=state_session, head=list(position), yaw=yaw, pitch=pitch,
                                                 blocking=getattr(self.heads, 'blocking', False)))
                    self.status = 'following-echo'
            except urllib.error.HTTPError as error:
                self.errors += 1; self.session = None
                # 400 = new world session (portal, respawn, rejoin): refresh at once so the lease does not lapse.
                self.status = f'bridge {error.code}'; self.stop.wait(.1 if error.code == 400 else 1 if error.code != 404 else 10)
            except Exception as error:  # noqa: BLE001
                self.errors += 1; self.session = None; bridge = None
                self.status = f'retrying: {type(error).__name__}'[:120]; self.stop.wait(1)
            self.stop.wait(max(0, self.interval-(time.monotonic()-started)))


def start_all(output_directory):
    alignment = load_alignment(); write_alignment_file(output_directory, alignment)
    write_alignment_file(LOCAL, alignment)  # read by EchoCraftPhysics.dll on client and server
    heads = EchoHeadFeed(); feeds = [heads, WorldStreamFeed(), BodyFeed(heads, alignment)]
    for feed in feeds: feed.start()
    return feeds
