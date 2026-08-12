import random
import string
import requests
import time
import threading
import os
import json
import socket
import subprocess
import asyncio
import zipfile
import tarfile
import shutil
import glob
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor

# ----------------------------------------------------------------
# AUTO INSTALL
# ----------------------------------------------------------------
import sys

def _ensure(pkg, import_as=None):
    name = import_as or pkg
    try:
        __import__(name)
    except ImportError:
        print(f"  installing {pkg}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

_ensure("requests")
_ensure("psutil")
_ensure("aiohttp")
_ensure("aiohttp-socks", "aiohttp_socks")

import psutil
import aiohttp
from aiohttp_socks import ProxyConnector

# ----------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------
LENGTHS              = [3, 4, 5]
MAX_RESULTS          = 1
REQUESTS_PER_CIRCUIT = 30
DELAY                = 0.0
BATCH_SIZE           = 25

BASE_PORT       = 9050
CTRL_PORT_BASE  = 9150

TOKEN_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
RL_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ratelimited.json")
TOR_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tor")
TOR_EXE     = os.path.join(TOR_DIR, "tor.exe")
W           = 67

POOL_SIZE = 8
WORKERS   = 24

# Tor Expert Bundle download URLs — .tar.gz format (Windows x64)
# Listed newest → oldest; the downloader tries each in order.
# Update the first entry when a new Tor Browser version is released.
TOR_BUNDLE_URLS = [
    "https://archive.torproject.org/tor-package-archive/torbrowser/15.0.19/tor-expert-bundle-windows-x86_64-15.0.19.tar.gz",
    "https://archive.torproject.org/tor-package-archive/torbrowser/15.0.18/tor-expert-bundle-windows-x86_64-15.0.18.tar.gz",
    "https://archive.torproject.org/tor-package-archive/torbrowser/15.0.17/tor-expert-bundle-windows-x86_64-15.0.17.tar.gz",
    "https://archive.torproject.org/tor-package-archive/torbrowser/14.5.9/tor-expert-bundle-windows-x86_64-14.5.9.tar.gz",
]

# ----------------------------------------------------------------
# COLORS / SYMBOLS
# ----------------------------------------------------------------
R  = "\033[0m";  B  = "\033[1m";  CY = "\033[96m"
GR = "\033[92m"; RD = "\033[91m"; YL = "\033[93m"
MG = "\033[95m"; DM = "\033[2m"

OK   = f"{GR}✔{R}";  FAIL = f"{RD}✘{R}"
DOT  = f"{MG}◆{R}";  ARR  = f"{CY}›{R}"
WARN = f"{YL}▲{R}"

def ts(): return f"{DM}[{time.strftime('%H:%M:%S')}]{R}"

def hr(c='─', col=DM, w=W): print(f"  {col}{c*w}{R}")
def section(lbl):
    s = f"  {lbl}  "; side = (W - len(s)) // 2
    print(f"\n  {DM}{'─'*side}{R}{CY}{B}{s}{R}{DM}{'─'*side}{R}\n")

# ----------------------------------------------------------------
# TOR AUTO-DOWNLOAD / INSTALL
# ----------------------------------------------------------------

def _find_tor_exe_in(directory):
    """Recursively search for tor.exe inside a directory."""
    for root, dirs, files in os.walk(directory):
        for fname in files:
            if fname.lower() == "tor.exe":
                return os.path.join(root, fname)
    return None

def _download_tor():
    """Download and extract Tor Expert Bundle (.tar.gz), placing tor.exe at TOR_EXE."""
    os.makedirs(TOR_DIR, exist_ok=True)
    tmp_archive = os.path.join(TOR_DIR, "_tor_bundle.tar.gz")
    tmp_extract = os.path.join(TOR_DIR, "_tor_extract_tmp")

    downloaded = False
    for url in TOR_BUNDLE_URLS:
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            got   = 0
            with open(tmp_archive, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = int(got / total * 40)
                            bar = f"{GR}{'█'*pct}{'░'*(40-pct)}{R}"
                            mb_got   = got   // 1024 // 1024
                            mb_total = total // 1024 // 1024
                            print(f"\r  {ARR}  [{bar}] {mb_got}MB / {mb_total}MB   ", end="", flush=True)
            print()
            downloaded = True
            break
        except Exception as e:
            print(f"  {WARN}  failed ({e}), trying next...")
            if os.path.exists(tmp_archive):
                os.remove(tmp_archive)

    if not downloaded:
        print(f"  {FAIL}  could not download tor automatically.")
        print(f"       download manually → https://www.torproject.org/download/tor/")
        print(f"       place tor.exe at  → {TOR_EXE}")
        return False

    # Extract .tar.gz
    if os.path.exists(tmp_extract):
        shutil.rmtree(tmp_extract, ignore_errors=True)
    os.makedirs(tmp_extract, exist_ok=True)

    print(f"  {ARR}  Initializing...")
    try:
        with tarfile.open(tmp_archive, "r:gz") as tf:
            tf.extractall(tmp_extract)
    except Exception as e:
        print(f"  {FAIL}  extraction failed: {e}")
        try: os.remove(tmp_archive)
        except: pass
        return False
    finally:
        if os.path.exists(tmp_archive):
            try: os.remove(tmp_archive)
            except: pass

    exe = _find_tor_exe_in(tmp_extract)
    exe_folder = os.path.dirname(exe)
    for item in os.listdir(exe_folder):
        src  = os.path.join(exe_folder, item)
        dest = os.path.join(TOR_DIR, item)
        try:
            if os.path.isfile(src):
                shutil.copy2(src, dest)
            elif os.path.isdir(src):
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
        except Exception as e:
            print(f"  {WARN}  could not copy {item}: {e}")

    shutil.rmtree(tmp_extract, ignore_errors=True)

    if os.path.isfile(TOR_EXE):
        return True
    else:
        print(f"  {FAIL}  tor.exe not found after extraction.")
        print(f"       expected path: {TOR_EXE}")
        return False

def ensure_tor_exe():
    """Make sure tor.exe exists; auto-download if missing."""
    if os.path.isfile(TOR_EXE):
        return True
    return _download_tor()

# ----------------------------------------------------------------
# TOR AUTO-START / STOP
# ----------------------------------------------------------------
_tor_proc = None

def _is_tor_running():
    try:
        with socket.create_connection(("127.0.0.1", BASE_PORT), timeout=1):
            return True
    except:
        return False

def start_tor():
    global _tor_proc

    if _is_tor_running():
        return True

    if not ensure_tor_exe():
        return False

    torrc_path = os.path.join(TOR_DIR, "torrc_auto")
    data_dir   = os.path.join(TOR_DIR, "data_auto")
    os.makedirs(data_dir, exist_ok=True)
    with open(torrc_path, "w") as f:
        f.write(
            f"SocksPort {BASE_PORT}\n"
            f"ControlPort {CTRL_PORT_BASE}\n"
            f"DataDirectory {data_dir}\n"
            "MaxCircuitDirtiness 10\n"
            "NewCircuitPeriod 10\n"
            "CircuitBuildTimeout 8\n"
            "LearnCircuitBuildTimeout 0\n"
            "NumEntryGuards 1\n"
            "KeepalivePeriod 10\n"
        )

    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _tor_proc = subprocess.Popen(
            [TOR_EXE, "-f", torrc_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **kwargs
        )
    except Exception as e:
        print(f"  {FAIL}  failed to start tor: {e}")
        return False

    deadline = time.time() + 40
    while time.time() < deadline:
        if _is_tor_running():
            return True
        time.sleep(0.5)

    print(f"  {FAIL}  tor didn't come up in time")
    _tor_proc.terminate()
    _tor_proc = None
    return False

def stop_tor():
    global _tor_proc
    if _tor_proc is not None:
        try:
            _tor_proc.terminate()
            _tor_proc.wait(timeout=5)
        except:
            pass
        _tor_proc = None
        print(f"\n  {DM}tor stopped{R}")

# ----------------------------------------------------------------
# TEMP FOLDER CLEANUP
# ----------------------------------------------------------------
# Tracks which socks ports are currently alive (used by live circuits).
_active_ports      = set()
_active_ports_lock = threading.Lock()

def _register_port(port):
    with _active_ports_lock:
        _active_ports.add(port)

def _unregister_port(port):
    with _active_ports_lock:
        _active_ports.discard(port)

def _cleanup_tor_temp_files():
    """
    Delete data_<port> folders and torrc_<port> files for circuits that are
    no longer active. Safe to call at any time — skips anything still in use.
    """
    try:
        with _active_ports_lock:
            alive = set(_active_ports)

        # data directories: tor/data_<port>/
        pattern_data = os.path.join(TOR_DIR, "data_*")
        for path in glob.glob(pattern_data):
            basename = os.path.basename(path)
            # skip the main "data_auto" folder used by start_tor()
            if basename == "data_auto":
                continue
            try:
                port = int(basename.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if port not in alive:
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass

        # torrc config files: tor/torrc_<port>
        pattern_rc = os.path.join(TOR_DIR, "torrc_*")
        for path in glob.glob(pattern_rc):
            basename = os.path.basename(path)
            if basename == "torrc_auto":
                continue
            try:
                port = int(basename.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            if port not in alive:
                try:
                    os.remove(path)
                except Exception:
                    pass
    except Exception:
        pass  # never crash the cleaner thread

def temp_cleaner(stop_evt, interval=60):
    """Background thread: cleans up dead circuit temp files every `interval` seconds."""
    while not stop_evt.is_set():
        _cleanup_tor_temp_files()
        stop_evt.wait(timeout=interval)
    # one final cleanup on exit
    _cleanup_tor_temp_files()

# ----------------------------------------------------------------
# HARDWARE DETECTION
# ----------------------------------------------------------------
def _detect_gpu():
    """Return (gpu_name, vram_mb) or (None, 0)."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=4
        ).decode().strip().splitlines()
        if out:
            parts = out[0].split(",")
            name  = parts[0].strip()
            vram  = int(parts[1].strip()) if len(parts) > 1 else 0
            return name, vram
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["wmic", "path", "win32_videocontroller",
             "get", "name,AdapterRAM", "/format:csv"],
            stderr=subprocess.DEVNULL, timeout=4
        ).decode().strip().splitlines()
        for line in out:
            cols = [c.strip() for c in line.split(",") if c.strip()]
            if len(cols) >= 3 and cols[1].isdigit():
                vram = int(cols[1]) // 1024**2
                return cols[2], vram
    except Exception:
        pass
    return None, 0

def detect_limits():
    free_mb  = psutil.virtual_memory().available / 1024**2
    total_mb = psutil.virtual_memory().total      / 1024**2
    cores    = psutil.cpu_count(logical=True) or 2
    gpu_name, gpu_vram = _detect_gpu()
    ram_c    = int(free_mb * 0.55 / 25)
    cpu_c    = cores * 4
    pool     = max(4, min(ram_c, cpu_c, 40)) + 6
    if gpu_name:
        worker_cap = 400
        pool       = min(pool + 4, 60)
    else:
        worker_cap = 200
    workers  = min(pool * 8, worker_cap)
    tier     = "low" if pool <= 6 else ("mid" if pool <= 18 else "high")
    return pool, workers, tier, total_mb, free_mb, cores, gpu_name, gpu_vram

# ----------------------------------------------------------------
# BANNER
# ----------------------------------------------------------------
def banner():
    os.system("cls")
    print()
    print(f"  {MG}{B}╔{'═'*W}╗{R}")
    print(f"  {MG}{B}║{'AURORA':^{W}}║{R}")
    print(f"  {MG}{B}║{' discord username generator':^{W}}║{R}")
    print(f"  {MG}{B}╚{'═'*W}╝{R}")
    print()
    print(f"  {DM}sigma mode: true   "
          f"workers: {WORKERS}   pool: {POOL_SIZE} circuits   ")
    print()

# ----------------------------------------------------------------
# CIRCUIT POOL
# ----------------------------------------------------------------
RL_RETIRE_THRESHOLD = 2

class Circuit:
    def __init__(self, socks_port, ctrl_port, proc, ip):
        self.socks_port = socks_port
        self.ctrl_port  = ctrl_port
        self.proc       = proc
        self.ip         = ip
        self.uses       = 0
        self.rl_hits    = 0
        self.lock       = threading.Lock()

    def socks_url(self):
        return f"socks5://127.0.0.1:{self.socks_port}"

    def kill(self):
        _unregister_port(self.socks_port)
        try: self.proc.terminate()
        except: pass

circuit_pool = Queue()
port_counter = 0
port_lock    = threading.Lock()

def next_ports():
    global port_counter
    with port_lock:
        idx = port_counter; port_counter += 1
    return BASE_PORT + 1 + idx, CTRL_PORT_BASE + 1 + idx

def _wait_socks(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except:
            time.sleep(0.15)
    return False

def _launch_circuit():
    sp, cp     = next_ports()
    data_dir   = os.path.join(TOR_DIR, f"data_{sp}")
    torrc_path = os.path.join(TOR_DIR, f"torrc_{sp}")
    os.makedirs(data_dir, exist_ok=True)

    torrc = (
        f"SocksPort {sp}\n"
        f"ControlPort {cp}\n"
        f"DataDirectory {data_dir}\n"
        "ExitPolicy reject *:*\n"
        "MaxCircuitDirtiness 10\n"
        "NewCircuitPeriod 10\n"
        "CircuitBuildTimeout 8\n"
        "LearnCircuitBuildTimeout 0\n"
        "NumEntryGuards 1\n"
        "KeepalivePeriod 10\n"
        "SocksTimeout 10\n"
        "ConnLimit 1024\n"
    )
    with open(torrc_path, "w") as f:
        f.write(torrc)

    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [TOR_EXE, "-f", torrc_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **kwargs
        )
    except:
        return None

    if not _wait_socks(sp):
        proc.terminate()
        return None

    proxy = {"http": f"socks5h://127.0.0.1:{sp}", "https": f"socks5h://127.0.0.1:{sp}"}

    _IP_CHECK_URLS = [
        ("https://api.ipify.org?format=json",   lambda js: js.get("ip", "?")),
        ("https://ifconfig.me/ip",               None),
        ("https://check.torproject.org/api/ip",  lambda js: js.get("IP", "?")),
    ]

    detected_ip = None
    for url, extractor in _IP_CHECK_URLS:
        try:
            r = requests.get(url, proxies=proxy, timeout=10)
            r.raise_for_status()
            if extractor is None:
                detected_ip = r.text.strip()
            else:
                js = r.json()
                if "IsTor" in js and not js["IsTor"]:
                    continue
                detected_ip = extractor(js)
            if detected_ip:
                break
        except Exception:
            continue

    if detected_ip is None:
        proc.terminate()
        return None

    c = Circuit(sp, cp, proc, detected_ip)
    _register_port(sp)
    return c

def _newnym(circuit):
    try:
        with socket.create_connection(("127.0.0.1", circuit.ctrl_port), timeout=3) as s:
            # Try unauthenticated first, then empty-password
            for auth_cmd in [b'AUTHENTICATE\r\n', b'AUTHENTICATE ""\r\n']:
                s.sendall(auth_cmd)
                resp = s.recv(1024).decode(errors="ignore")
                if resp.startswith("250"):
                    break
                # flush any error and retry
            else:
                return False  # neither auth worked

            s.sendall(b"SIGNAL NEWNYM\r\n")
            s.recv(1024)
            time.sleep(1.0)  # give tor a moment to build new circuit

        # Update IP using fast endpoint
        proxy = {"http":  f"socks5h://127.0.0.1:{circuit.socks_port}",
                 "https": f"socks5h://127.0.0.1:{circuit.socks_port}"}
        try:
            r = requests.get("https://api.ipify.org?format=json", proxies=proxy, timeout=8)
            circuit.ip = r.json().get("ip", circuit.ip)
        except Exception:
            pass  # keep old IP, circuit is still usable
        circuit.uses = 0
        return True
    except Exception:
        return False

def pool_refiller(stop_evt):
    _refill_lock = threading.Lock()   # prevent overlapping refill waves

    def _launch_into_pool(count):
        """Spawn `count` circuit launchers; each feeds the pool immediately on success."""
        added = [0]
        lk    = threading.Lock()

        def _one():
            c = _launch_circuit()
            if c:
                if circuit_pool.qsize() >= POOL_SIZE:
                    c.kill()
                    return
                circuit_pool.put(c)
                with lk:
                    added[0] += 1
                    with print_lock:
                        global _rl_status_live
                        _rl_status_live = False
                        print(f"  {ts()}  {OK}  {CY}+1 circuit{R}  {DM}(pool: {circuit_pool.qsize()}){R}")

        threads = [threading.Thread(target=_one, daemon=True) for _ in range(count)]
        for t in threads: t.start()
        # don't join — fire and forget so refiller loop keeps running
        return threads

    _active_threads = []

    while not stop_evt.is_set():
        # prune finished threads
        _active_threads = [t for t in _active_threads if t.is_alive()]

        current = circuit_pool.qsize()
        needed  = POOL_SIZE - current

        if needed > 0 and len(_active_threads) == 0:
            # overshoot to compensate for failures; more aggressive when pool is very low
            overshoot = POOL_SIZE * 2 if needed >= POOL_SIZE // 2 else needed + 4
            new_threads = _launch_into_pool(overshoot)
            _active_threads.extend(new_threads)
            if needed >= POOL_SIZE // 2:
                with print_lock:
                    _rl_status_live = False

        time.sleep(0.1)


def get_circuit(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try: return circuit_pool.get(timeout=2)
        except Empty: pass
    return None

def return_circuit(c):
    c.uses += 1
    if c.uses >= REQUESTS_PER_CIRCUIT:
        if not _newnym(c):
            c.kill()
            return
    circuit_pool.put(c)

def retire_circuit(c):
    c.kill()

def prime_pool(n):
    ready = []
    lk    = threading.Lock()

    def _one():
        c = _launch_circuit()
        if c:
            with lk:
                if len(ready) >= n:
                    c.kill()
                    return
                ready.append(c)
                print(f"  {ts()}  {OK}  Proxy {len(ready):>2}/{n}   {DM}{c.ip}{R}")

    attempt      = 0
    GIVE_UP_ZERO = 6   # bail only if ZERO succeed after this many attempts

    while len(ready) < n:
        attempt      += 1
        still_needed  = n - len(ready)

        if attempt > 1:
            print(f"  {ts()}   {WARN}  {len(ready):>2}/{n} — retrying {still_needed} failed slots...")
            time.sleep(2)  # brief pause before retry wave so Tor network can recover

        # Launch 2× needed in parallel to compensate for failures
        over = min(still_needed * 2, still_needed + 8)
        threads = [threading.Thread(target=_one, daemon=True) for _ in range(over)]
        for t in threads: t.start()
        for t in threads: t.join()

        if len(ready) == 0 and attempt >= GIVE_UP_ZERO:
            print(f"\n  {FAIL}  no circuits after {attempt} attempts — check tor.exe and network")
            return False

    for c in ready[:n]:   # cap at n if over-launched
        circuit_pool.put(c)
    print(f"\n  {OK}  loaded {min(len(ready), n)}/{n} proxies")
    return True

# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------
def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    return None

def get_headers(token):
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if token: h["Authorization"] = token
    return h

print_lock   = threading.Lock()
counter_lock = threading.Lock()
counters     = {"checked": 0, "available": 0, "rl": 0}

_rl_total       = 0
_rl_status_live = False

RL_SURGE_THRESHOLD = 15
RL_SURGE_CIRCUITS  = 8

def _print_rl_hit():
    global _rl_total, _rl_status_live
    _rl_total += 1
    line = f"  {ts()}  {DM}{WARN}  Generating... {R}"
    if _rl_status_live:
        print(f"\033[1A\033[2K{line}")
    else:
        print(line)
        _rl_status_live = True

    if _rl_total >= RL_SURGE_THRESHOLD:
        _rl_total = 0
        _rl_status_live = False
        def _surge():
            added = []
            lk = threading.Lock()
            def _one():
                c = _launch_circuit()
                if c:
                    with lk:
                        added.append(c)
            ts_ = [threading.Thread(target=_one, daemon=True) for _ in range(RL_SURGE_CIRCUITS)]
            for t in ts_: t.start()
            for t in ts_: t.join()
            if not added:
                return
            for c in added:
                circuit_pool.put(c)
        threading.Thread(target=_surge, daemon=True).start()

def gen_username(length):
    return random.choice(string.ascii_lowercase) + \
           ''.join(random.choice(string.ascii_lowercase + string.digits + "_.") for _ in range(length - 1))

# ----------------------------------------------------------------
# DOUBLE VERIFICATION
# ----------------------------------------------------------------
DISCORD_ATTEMPT_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
DISCORD_PROFILE_URL = "https://discord.com/api/v9/users/{}"

def _verify_available(username, headers, socks_port):
    proxy = {
        "http":  f"socks5h://127.0.0.1:{socks_port}",
        "https": f"socks5h://127.0.0.1:{socks_port}",
    }
    try:
        r1 = requests.post(
            DISCORD_ATTEMPT_URL,
            headers=headers,
            json={"username": username},
            proxies=proxy,
            timeout=5,
        )
        if r1.status_code == 429:
            return None, 429
        if r1.status_code != 200:
            return None, r1.status_code
        if r1.json().get("taken", True):
            return False, 200

        r2 = requests.get(
            DISCORD_PROFILE_URL.format(username),
            headers={"User-Agent": "Mozilla/5.0"},
            proxies=proxy,
            timeout=5,
        )
        if r2.status_code == 404:
            return True, 200
        if r2.status_code == 200:
            return False, 200
        return None, r2.status_code

    except Exception:
        return None, 0

# ----------------------------------------------------------------
# ASYNC FIRST-PASS
# ----------------------------------------------------------------
async def _check_one_async(session, username, headers):
    try:
        async with session.post(
            DISCORD_ATTEMPT_URL,
            headers=headers,
            json={"username": username},
            timeout=aiohttp.ClientTimeout(total=4),
        ) as r:
            if r.status == 200:
                js = await r.json()
                return not js.get("taken", True), 200
            return None, r.status
    except:
        return None, 0

async def run_batch_async(circuit, usernames, headers):
    connector = ProxyConnector.from_url(circuit.socks_url())
    results   = []
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [_check_one_async(session, u, headers) for u in usernames]
        raw   = await asyncio.gather(*tasks, return_exceptions=True)
        for u, r in zip(usernames, raw):
            if isinstance(r, Exception):
                results.append((u, None, 0))
            else:
                results.append((u, r[0], r[1]))
    return results

# ----------------------------------------------------------------
# WORKER
# ----------------------------------------------------------------
def check_batch(length, headers, found_results, stop_evt):
    global _rl_status_live
    while not stop_evt.is_set():
        if len(found_results) >= MAX_RESULTS:
            stop_evt.set()
            break

        circuit = get_circuit(timeout=30)
        if circuit is None:
            continue

        usernames = [gen_username(length) for _ in range(BATCH_SIZE)]

        try:
            loop  = asyncio.new_event_loop()
            batch = loop.run_until_complete(run_batch_async(circuit, usernames, headers))
            loop.close()
        except:
            retire_circuit(circuit)
            continue

        rl_hit     = False
        candidates = []

        for username, result, status in batch:
            if stop_evt.is_set():
                break
            if status == 429:
                with counter_lock: counters["rl"] += 1
                circuit.rl_hits += 1
                rl_hit = True
                with print_lock:
                    _print_rl_hit()
                if circuit.rl_hits >= RL_RETIRE_THRESHOLD:
                    break
                continue
            if result is None:
                continue
            with counter_lock: counters["checked"] += 1
            if result is True:
                candidates.append(username)
            else:
                ip = f"{DM}[{circuit.ip}]{R}"
                u  = f"{username:<14}"
                with print_lock:
                    _rl_status_live = False
                    print(f"  {ts()}  {FAIL}  {u}  {ip}")

        for username in candidates:
            if stop_evt.is_set():
                break

            ip = f"{DM}[{circuit.ip}]{R}"
            u  = f"{CY}{B}{username:<14}{R}"
            with print_lock:
                _rl_status_live = False
                print(f"  {ts()}  {YL}?{R}   {u}  {ip}  {DM}verifying...{R}")

            confirmed, vstatus = _verify_available(username, headers, circuit.socks_port)

            if vstatus == 429:
                with counter_lock: counters["rl"] += 1
                rl_hit = True
                with print_lock:
                    _print_rl_hit()
                continue

            if confirmed is True:
                with counter_lock: counters["available"] += 1
                u_ok = f"{GR}{B}{username:<14}{R}"
                with print_lock:
                    _rl_status_live = False
                    print(f"\n  {ts()}  {OK}  {u_ok}  {ip}  {GR}{B}✔ AVAILABLE{R}\n")
                found_results.append(username)
                if len(found_results) >= MAX_RESULTS:
                    stop_evt.set()
                break
            elif confirmed is False:
                u_fp = f"{username:<14}"
                with print_lock:
                    print(f"  {ts()}  {FAIL}  {u_fp}  {ip}  {DM}(false positive){R}")
            else:
                with print_lock:
                    print(f"  {ts()}  {WARN}  {CY}{username:<14}{R}  {ip}  {DM}verify inconclusive{R}")

        if rl_hit:
            retire_circuit(circuit)
        else:
            return_circuit(circuit)

# ----------------------------------------------------------------
# RUN PER LENGTH
# ----------------------------------------------------------------
def run_for_length(length, headers, shared_results, global_stop):
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(check_batch, length, headers, shared_results, global_stop)
                for _ in range(WORKERS)]
        for f in futs:
            try: f.result()
            except: pass

# ----------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------
def ask_config():
    global LENGTHS, MAX_RESULTS
    print()
    while True:
        try:
            raw = input(f"  {CY}length{R} › {R}").strip()
            lengths = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if lengths and all(1 <= l <= 5 for l in lengths):
                LENGTHS = lengths
                break
        except ValueError:
            pass
        print(f"  {WARN}  enter numbers 1-5 separated by commas")
    while True:
        try:
            n = int(input(f"  {CY}how many usernames{R} › {R}").strip())
            if n >= 1:
                MAX_RESULTS = n
                break
        except ValueError:
            pass
        print(f"  {WARN}  enter a number ≥ 1")
    print()

def main():
    global POOL_SIZE, WORKERS

    os.system("color")
    POOL_SIZE, WORKERS, tier, total_mb, free_mb, cores, gpu_name, gpu_vram = detect_limits()
    banner()

    IW = W - 2
    def _hw_row(label, val, vcol=""):
        pad = IW - 2 - len(label) - len(val)
        return (f"  {MG}◆{R}  {DM}{label}{R}  "
                f"{vcol}{val}{R}{' '*max(pad,0)} {MG}◆{R}")

    gpu_val  = (f"{GR}{gpu_name}  {gpu_vram} MB VRAM{R}"
                if gpu_name else f"{DM}not detected (cpu mode){R}")
    gpu_raw  = (f"{gpu_name}  {gpu_vram} MB VRAM"
                if gpu_name else "not detected (cpu mode)")
    tier_val = f"{tier}  →  {POOL_SIZE} tor instances · {WORKERS} workers"

    title    = " hardware scan "
    top_bar  = f""

    ram_row  = _hw_row("RAM  ", f"{free_mb:.0f} MB free / {total_mb:.0f} MB total", DM)
    cpu_row  = _hw_row("CPU  ", f"{cores} logical cores", DM)
    gpu_vcol = GR if gpu_name else DM
    gpu_row  = _hw_row("GPU  ", gpu_raw, gpu_vcol)
    tier_row = _hw_row("TIER ", tier_val, CY)
    bot_bar  = f""

    print()
    print(top_bar)
    print(ram_row)
    print(cpu_row)
    print(gpu_row)
    print(tier_row)
    print(bot_bar)
    print()

    ask_config()
    if not start_tor():
        input(f"\n  {DM}press enter to exit...{R}  ")
        return
    print()

    token   = load_token()
    headers = get_headers(token)

    if not prime_pool(POOL_SIZE):
        stop_tor()
        input(f"\n  {DM}press enter to exit...{R}  ")
        return

    print(); hr()

    pool_stop    = threading.Event()
    clean_stop   = threading.Event()
    global_stop  = threading.Event()
    all_results  = []

    threading.Thread(target=pool_refiller, args=(pool_stop,), daemon=True).start()
    # temp cleaner runs every 60 seconds
    threading.Thread(target=temp_cleaner, args=(clean_stop, 60), daemon=True).start()

    for length in LENGTHS:
        if global_stop.is_set():
            break
        global_stop.clear()
        run_for_length(length, headers, all_results, global_stop)
        if len(all_results) >= MAX_RESULTS:
            break

    pool_stop.set()
    global_stop.set()
    clean_stop.set()  # triggers one final cleanup

    while not circuit_pool.empty():
        try:
            c = circuit_pool.get_nowait()
            c.kill()
        except:
            break

    stop_tor()

    # final cleanup pass after everything is killed
    time.sleep(0.5)
    _cleanup_tor_temp_files()

    print()
    print(f"  {MG}{B}╔{'═'*W}╗{R}")
    print(f"  {MG}{B}║{'results':^{W}}║{R}")
    print(f"  {MG}{B}╚{'═'*W}╝{R}")
    print()
    for name in all_results:
        print(f"  {DOT}  {GR}{B}{name}{R}")
    print()
    print(f"  {DM}total: {GR}{B}{len(all_results)}{R}{DM} / {MAX_RESULTS}   {counters['checked']} checked{R}")
    print()
    input(f"  {DM}press enter to exit...{R}  ")

if __name__ == "__main__":
    main()
