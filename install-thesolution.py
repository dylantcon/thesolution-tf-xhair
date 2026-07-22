#!/usr/bin/env python3
"""
Installs the "thesolution" crosshair into Team Fortress 2.

Easiest way: double-click INSTALL.bat

Or from a terminal, in this folder:

    python install-thesolution.py                  # finds TF2 by itself
    python install-thesolution.py "<TF2 folder>"   # if you'd rather say where

The path can be the TF2 root, the "tf" folder, or "tf\\custom".
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import time

PAYLOAD = "thesolution"          # folder we copy into tf/custom
APPID = "440"                    # TF2's Steam app id
CONVARS = ['cl_crosshair_file ""', "crosshair 1"]


# --------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------

def say(msg=""):
    """print() that survives old Windows consoles that can't encode a path."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def ask_yes(question, default=True):
    """y/n prompt that doesn't explode when there's no keyboard attached."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        say()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def pause():
    """Keep the window open when this was launched by double-clicking."""
    try:
        input("\nPress Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass


def bail(msg):
    say()
    say("PROBLEM:")
    for line in msg.splitlines():
        say("  " + line)
    pause()
    sys.exit(1)


# --------------------------------------------------------------------
# Finding the files we ship
# --------------------------------------------------------------------

def find_payload():
    """Locate the 'thesolution' folder, relative to this script -- not the
    working directory, so it works no matter where it's run from."""
    here = os.path.dirname(os.path.abspath(__file__))

    # Normal: script sits next to the folder. Fallback: script moved inside it.
    for candidate in (os.path.join(here, PAYLOAD), here):
        if os.path.isdir(os.path.join(candidate, "materials")) and \
           os.path.isdir(os.path.join(candidate, "scripts")):
            return candidate
    return None


# --------------------------------------------------------------------
# Finding TF2
# --------------------------------------------------------------------

def registry_steam_paths():
    """Ask Windows where Steam is. Most reliable method by far."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    keys = [
        (winreg.HKEY_CURRENT_USER,  r"Software\Valve\Steam",             "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam",             "InstallPath"),
    ]
    out = []
    for hive, subkey, value in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path = winreg.QueryValueEx(key, value)[0]
                if path:
                    # The registry hands back things like "c:/program files/steam"
                    out.append(os.path.normpath(path))
        except OSError:
            pass
    return out


def steam_libraries():
    """Every folder Steam might be installing games into."""
    roots = registry_steam_paths() + [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        os.path.expanduser("~/.local/share/Steam"),                     # Linux
        os.path.expanduser("~/.steam/steam"),                           # Linux
        os.path.expanduser("~/Library/Application Support/Steam"),      # macOS
    ]

    libraries, seen = [], set()

    def add(path):
        if not path:
            return
        path = os.path.normpath(path)
        key = os.path.normcase(path)
        if key not in seen and os.path.isdir(path):
            seen.add(key)
            libraries.append(path)

    for root in roots:
        add(root)

    # Each Steam install lists its other library drives here. Two formats exist
    # depending on Steam version, so match both:
    #   old:  "1"      "D:\\SteamLibrary"
    #   new:  "path"   "D:\\SteamLibrary"
    for root in list(libraries):
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        try:
            with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for match in re.findall(r'"(?:path|\d+)"\s+"([^"]+)"', text):
            add(match.replace("\\\\", "\\"))

    return libraries


def looks_like_tf2(path):
    """Is this really a TF2 install and not just a folder with the right name?"""
    tf = os.path.join(path, "tf")
    return os.path.isdir(tf) and (
        os.path.isfile(os.path.join(tf, "gameinfo.txt")) or
        os.path.isdir(os.path.join(tf, "cfg"))
    )


def find_tf2_installs():
    """Look for TF2 in every Steam library. Returns a list -- someone can
    genuinely have more than one."""
    found, seen = [], set()

    for lib in steam_libraries():
        steamapps = os.path.join(lib, "steamapps")

        # appmanifest_440.acf is Steam's own record of the install. It names
        # the folder, so we don't have to guess.
        names = []
        acf = os.path.join(steamapps, "appmanifest_%s.acf" % APPID)
        if os.path.isfile(acf):
            try:
                with open(acf, "r", encoding="utf-8", errors="ignore") as f:
                    match = re.search(r'"installdir"\s+"([^"]+)"', f.read())
                if match:
                    names.append(match.group(1))
            except OSError:
                pass
        names.append("Team Fortress 2")   # fallback if the manifest is missing

        for name in names:
            candidate = os.path.join(steamapps, "common", name)
            key = os.path.normcase(os.path.normpath(candidate))
            if key not in seen and looks_like_tf2(candidate):
                seen.add(key)
                found.append(candidate)

    return found


def resolve_custom_dir(path):
    """
    Turn whatever path we were given into the real tf/custom folder.
    Returns (custom_dir, tf_dir), or (None, None) if it isn't TF2.
    """
    if not path:
        return None, None

    # Windows quirk: a quoted path ending in a backslash arrives with a
    # stray quote glued on, e.g.  C:\Games\Team Fortress 2"
    path = path.strip().strip('"').strip("'").strip()
    if not path:
        return None, None
    path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))

    name = os.path.basename(path).lower()
    if name == "custom":
        tf = os.path.dirname(path)
    elif name == "tf":
        tf = path
    elif os.path.isdir(os.path.join(path, "tf")):
        tf = os.path.join(path, "tf")          # the TF2 root
    elif os.path.isdir(os.path.join(path, "Team Fortress 2", "tf")):
        tf = os.path.join(path, "Team Fortress 2", "tf")   # steamapps/common
    else:
        return None, None

    if not (os.path.isdir(os.path.join(tf, "cfg")) or
            os.path.isfile(os.path.join(tf, "gameinfo.txt"))):
        return None, None

    return os.path.join(tf, "custom"), tf


def choose_tf2():
    """Work out which TF2 to install into, asking only if we have to."""
    installs = find_tf2_installs()

    if len(installs) == 1:
        say("Found TF2: %s" % installs[0])
        return resolve_custom_dir(installs[0])

    if len(installs) > 1:
        say("Found more than one TF2 install:")
        for i, path in enumerate(installs, 1):
            say("  %d) %s" % (i, path))
        say()
        while True:
            try:
                choice = input("Which one? [1] ").strip() or "1"
            except (EOFError, KeyboardInterrupt):
                choice = "1"
            if choice.isdigit() and 1 <= int(choice) <= len(installs):
                return resolve_custom_dir(installs[int(choice) - 1])
            say("  Type a number between 1 and %d." % len(installs))

    # Nothing found -- ask, rather than giving up.
    say("Couldn't find TF2 automatically.")
    say()
    say("In Steam: right-click Team Fortress 2 -> Manage -> Browse local files.")
    say("Then copy the path out of the address bar and paste it here.")
    say()
    for _ in range(3):
        try:
            typed = input("TF2 folder (or blank to give up): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not typed:
            break
        custom_dir, tf_dir = resolve_custom_dir(typed)
        if custom_dir:
            return custom_dir, tf_dir
        say("  That doesn't look like a TF2 folder. It should contain a 'tf' folder.")
    return None, None


# --------------------------------------------------------------------
# Safety checks before we touch anything
# --------------------------------------------------------------------

def tf2_is_running():
    """
    TF2 holds its files open, so installing over a running game half-works.

    Matched exactly, not as a substring -- "tf.exe" appearing somewhere inside
    an unrelated process name would nag him for no reason.
    """
    names = {"tf_win64.exe", "tf.exe", "hl2.exe", "hl2_linux", "hl2_osx"}
    try:
        if os.name == "nt":
            # CSV, no header: "tf_win64.exe","1234","Console","1","2,000 K"
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True, timeout=15).stdout
            running = {line.split('","')[0].lstrip('"').strip().lower()
                       for line in out.splitlines() if line.strip()}
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="],
                                 capture_output=True, text=True, timeout=15).stdout
            running = {os.path.basename(line).strip().lower()
                       for line in out.splitlines() if line.strip()}
    except Exception:
        return False       # can't tell -- never block the install over it
    return bool(names & running)


def check_writable(directory):
    """Confirm we can actually write there before copying 80 files."""
    probe = os.path.join(directory, ".thesolution_write_test")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def find_script_conflicts(custom_dir, tf_dir):
    """
    Other mods in custom/ that also ship weapon scripts will fight with this
    one. Worth telling him about instead of leaving him confused.
    """
    conflicts = []
    if not os.path.isdir(custom_dir):
        return conflicts

    for entry in sorted(os.listdir(custom_dir)):
        if entry == PAYLOAD or entry.lower().endswith((".vpk", ".cache", ".txt")):
            continue
        scripts = os.path.join(custom_dir, entry, "scripts")
        if not os.path.isdir(scripts):
            continue
        try:
            if any(f.lower().startswith("tf_weapon_") and f.lower().endswith(".txt")
                   for f in os.listdir(scripts)):
                conflicts.append(entry)
        except OSError:
            pass
    return conflicts


# --------------------------------------------------------------------
# Installing
# --------------------------------------------------------------------

class InstallError(Exception):
    """Something went wrong, but nothing was left half-applied."""


def force_rmtree(path):
    """rmtree that copes with the read-only files Windows likes to leave."""
    if not os.path.isdir(path):
        return

    def retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)
    except OSError:
        pass


def sweep_stale(custom_dir):
    """Clear temp folders left behind if a previous run was killed mid-way."""
    try:
        entries = os.listdir(custom_dir)
    except OSError:
        return
    for entry in entries:
        if entry.startswith((".%s.new-" % PAYLOAD, ".%s.old-" % PAYLOAD)):
            force_rmtree(os.path.join(custom_dir, entry))


def install_atomic(src, custom_dir):
    """
    Install all-or-nothing.

    A plain copy into the live folder can die halfway -- antivirus grabs a
    file, TF2 has one open, the disk fills -- and leave weapon scripts
    pointing at a texture that never arrived. So instead:

        1. build a complete copy in a temp folder next door
        2. verify it before it counts for anything
        3. swap it into place with renames, which are near-instant
        4. on any failure, put back exactly what was there before

    The temp folder is inside tf/custom on purpose: renames are only atomic
    within one filesystem, and a temp dir on C: couldn't be renamed onto a
    TF2 install sitting on D:.
    """
    dest = os.path.join(custom_dir, PAYLOAD)
    staging = os.path.join(custom_dir, ".%s.new-%d" % (PAYLOAD, os.getpid()))
    aside = os.path.join(custom_dir, ".%s.old-%d" % (PAYLOAD, os.getpid()))

    os.makedirs(custom_dir, exist_ok=True)
    sweep_stale(custom_dir)
    force_rmtree(staging)
    force_rmtree(aside)

    moved_old = False
    try:
        # 1. Build the copy off to the side. Nothing live is touched yet, so
        #    a failure here costs nothing but a temp folder.
        last_error = None
        for attempt in range(3):
            try:
                force_rmtree(staging)
                shutil.copytree(src, staging)
                last_error = None
                break
            except OSError as err:
                last_error = err
                if attempt < 2:
                    time.sleep(1.5)   # antivirus and Steam both hold files briefly
        if last_error is not None:
            raise InstallError("couldn't stage the files: %s" % last_error)

        # 2. Prove the copy is complete before it becomes the real install.
        problems = verify(src, staging)
        if problems:
            raise InstallError("staged copy came out wrong (%s)" % problems[0])

        # 3. Swap. Move the old install aside first -- on Windows you can't
        #    rename onto an existing non-empty folder.
        had_old = os.path.isdir(dest)
        if had_old:
            os.rename(dest, aside)
            moved_old = True

        try:
            os.rename(staging, dest)
        except OSError as err:
            raise InstallError("couldn't swap the new files in: %s" % err)

        # 4. Committed. The old copy is now dead weight.
        if moved_old:
            force_rmtree(aside)
            moved_old = False

        return dest, had_old

    except BaseException:
        # Any failure at all -- including Ctrl+C -- unwinds to the state we
        # found things in.
        force_rmtree(staging)
        if moved_old and os.path.isdir(aside) and not os.path.isdir(dest):
            try:
                os.rename(aside, dest)      # put his old install back
            except OSError:
                pass
        raise


def verify(src, dest):
    """Check every file actually arrived, at the right size. Returns a list
    of problems -- empty means the install is genuinely good."""
    problems = []
    for root, _, files in os.walk(src):
        for name in files:
            source_file = os.path.join(root, name)
            relative = os.path.relpath(source_file, src)
            target = os.path.join(dest, relative)
            if not os.path.isfile(target):
                problems.append("missing: " + relative)
            elif os.path.getsize(target) != os.path.getsize(source_file):
                problems.append("wrong size: " + relative)
    return problems


def ensure_convars(tf_dir):
    """
    Add the settings the crosshair needs to autoexec.cfg, which TF2 runs at
    startup. Skips anything already set, and backs the file up first.
    """
    cfg_dir = os.path.join(tf_dir, "cfg")
    cfg = os.path.join(cfg_dir, "autoexec.cfg")
    existing = ""

    if os.path.isfile(cfg):
        try:
            with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
                existing = f.read()
        except OSError as err:
            say("      couldn't read autoexec.cfg (%s)" % err)
            return False

    # Match on the convar name, not the whole line, so we never duplicate a
    # setting he's deliberately set to something else.
    missing = [c for c in CONVARS
               if not re.search(r"^\s*" + re.escape(c.split()[0]) + r"\b",
                                existing, re.M)]

    if not missing:
        say("      autoexec.cfg already covers this, left it alone")
        return True

    # Back up only when we're about to change something, and never overwrite
    # an older backup -- that one is the true original.
    if existing:
        backup = cfg + ".bak"
        if os.path.exists(backup):
            say("      autoexec.cfg.bak already exists, kept it")
        else:
            try:
                shutil.copy2(cfg, backup)
                say("      backed up autoexec.cfg to autoexec.cfg.bak")
            except OSError as err:
                say("      couldn't back up autoexec.cfg (%s)" % err)
                return False

    # Build the whole new file in memory, then swap it in. Appending in place
    # means a crash mid-write leaves his config truncated -- this way the file
    # is either the old one or the new one, never something in between.
    updated = existing
    if updated and not updated.endswith("\n"):
        updated += "\n"
    updated += "\n// thesolution crosshair\n" + "".join(c + "\n" for c in missing)

    try:
        os.makedirs(cfg_dir, exist_ok=True)
        temp = cfg + ".tmp-%d" % os.getpid()
        with open(temp, "w", encoding="utf-8") as f:
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())     # on disk before we swap, not just buffered
        os.replace(temp, cfg)        # atomic on Windows and everywhere else
    except OSError as err:
        say("      couldn't write autoexec.cfg (%s)" % err)
        try:
            os.remove(temp)
        except (OSError, NameError):
            pass
        return False

    say("      added: " + ", ".join(missing))
    return True


# --------------------------------------------------------------------

def run():
    say()
    say("thesolution crosshair installer")
    say("-" * 46)

    if sys.version_info < (3, 8):
        bail("This needs Python 3.8 or newer -- yours is %d.%d.\n"
             "Get a current one from python.org." % sys.version_info[:2])

    src = find_payload()
    if not src:
        bail("Can't find the 'thesolution' folder.\n"
             "This script has to stay in the same folder as it.\n"
             "If you only unzipped part of the download, unzip all of it.")

    # --- where does it go ---
    if len(sys.argv) > 1:
        given = " ".join(sys.argv[1:])
        custom_dir, tf_dir = resolve_custom_dir(given)
        if not custom_dir:
            bail("That doesn't look like a TF2 folder:\n"
                 "  %s\n"
                 "\n"
                 "In Steam: right-click Team Fortress 2 -> Manage ->\n"
                 "Browse local files, then copy the address bar." % given)
    else:
        custom_dir, tf_dir = choose_tf2()
        if not custom_dir:
            bail("No TF2 folder, nothing to install into.\n"
                 "Run it again with the path:\n"
                 '  python install-thesolution.py "C:\\path\\to\\Team Fortress 2"')

    say()
    say("Installing to: %s" % os.path.join(custom_dir, PAYLOAD))
    say()

    # --- checks before touching anything ---
    if tf2_is_running():
        say("  TF2 looks like it's running. It locks its files, so the install")
        say("  can half-apply and act strange.")
        if not ask_yes("  Close TF2, then continue. Ready?"):
            bail("Stopped. Close TF2 and run this again.")

    if not check_writable(custom_dir):
        bail("Windows won't let me write to:\n"
             "  %s\n"
             "\n"
             "Fix: right-click INSTALL.bat -> Run as administrator.\n"
             "(Happens when TF2 sits in Program Files with locked-down permissions.)"
             % custom_dir)

    # --- copy (all-or-nothing) ---
    try:
        dest, already_there = install_atomic(src, custom_dir)
    except (InstallError, OSError) as err:
        bail("Install failed: %s\n"
             "\n"
             "Nothing was changed -- you're exactly where you started, so it's\n"
             "safe to just try again.\n"
             "\n"
             "Usually this means TF2 or Steam is still open, or antivirus\n"
             "grabbed a file mid-copy. Close both and re-run." % err)

    # Staging was verified before the swap; confirm the live folder too.
    problems = verify(src, dest)
    if problems:
        bail("The files moved into place but don't check out (%s).\n"
             "Something is modifying %s underneath us -- antivirus is the\n"
             "usual culprit. Delete that folder and try again." % (problems[0], dest))

    total = sum(len(files) for _, _, files in os.walk(dest))
    say("  [1/2] %s %d files, all verified"
        % ("updated," if already_there else "copied", total))

    # --- crosshair settings ---
    say("  [2/2] crosshair settings")
    say("        TF2's own crosshair draws on top of this one and hides it.")
    if ask_yes("        Turn it off for you?"):
        if not ensure_convars(tf_dir):
            say("      do it by hand instead -- console (~ key):")
            for c in CONVARS:
                say("        " + c)
    else:
        say("      skipped -- open the console (~ key) in game and run:")
        for c in CONVARS:
            say("        " + c)

    # --- warn about mods that fight with this ---
    conflicts = find_script_conflicts(custom_dir, tf_dir)
    if conflicts:
        say()
        say("  HEADS UP: these also change weapon scripts, so one of them and")
        say("  this crosshair will be fighting over the same files:")
        for c in conflicts:
            say("    - %s" % c)
        say("  If the crosshair doesn't show up, that's the reason.")

    say()
    say("-" * 46)
    say("Done. Fully close TF2 and reopen it.")
    say()
    say("To uninstall: delete %s" % dest)
    pause()


def main():
    # A crash must never make a double-clicked window vanish before it can be
    # read -- otherwise all he sees is a black box blinking shut.
    try:
        run()
    except SystemExit:
        raise
    except Exception as err:
        say()
        say("Unexpected error: %s: %s" % (type(err).__name__, err))
        say()
        say("Send this to me and I'll sort it out.")
        import traceback
        traceback.print_exc()
        pause()
        sys.exit(1)


if __name__ == "__main__":
    main()
