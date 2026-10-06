# EchoCraft Installer

Play real Minecraft inside **Echo VR**: fly it like Echo, play it like Vivecraft. This installer sets up
[EchoCraft](https://github.com/nmdurkee/Echocraft) on your PC and connects you to the EchoCraft Minecraft server.

> ⚠️ **Experimental.** EchoCraft is an early hobby project. Expect rough edges. Your original Echo folder is kept
> untouched as a backup, and you can uninstall at any time.

## What you need first

- **Windows 10/11** and a PC VR headset that already runs Echo VR.
- **Community Echo VR working.** You must already be able to play Echo online (Echo installed at the default Meta
  location, with the community plugin loader `bin\win10\dbgcore.dll`). The installer does not set up Echo itself.
- **Minecraft Java Edition** on a Microsoft account.
- About **as much free disk space as your Echo folder** (the installer keeps a full backup copy) plus ~1 GB.
- An internet connection during install.

## Install

1. Download this repository (**Code ▸ Download ZIP**) and unzip it anywhere.
2. Close Echo VR, the Meta Horizon app, and any Minecraft launcher.
3. Double-click **`Install-EchoCraft.cmd`** and accept the administrator prompt (Echo lives under Program Files).
4. Follow the window. Near the end, **Prism Launcher** opens:
   1. Click the account button (top right) ▸ **Manage Accounts** ▸ **Add Microsoft** and sign in.
   2. Select the **EchoCraft** instance and click **Launch**. The first launch downloads Minecraft. When you reach
      the title screen, close Minecraft and Prism.
   3. Go back to the installer window and press Enter.

## What the installer does

| Step | Details |
|---|---|
| Checks Echo | Verifies `echovr.exe` is the final Echo VR client and that the community plugin loader is present (warns if your loader version differs from the tested one). |
| Backs up Echo | Renames `ready-at-dawn-echo-arena` to **`ready-at-dawn-echo-arena (original_backup)`**, then copies it back to the original name. EchoCraft only ever modifies the copy. |
| Downloads | [Prism Launcher](https://prismlauncher.org/) 11.1.1 (portable), [Eclipse Temurin](https://adoptium.net/) Java 21, [Python](https://www.python.org/) 3.14 (embeddable), [Fabric API](https://modrinth.com/mod/fabric-api) and [Vivecraft](https://github.com/Vivecraft/VivecraftMod) 1.21.1-1.3.15, from their official sources. Each file is checked against a pinned SHA-256 (`installer.json`). |
| Installs EchoCraft | Into `%LOCALAPPDATA%\EchoCraft\app`: the EchoCraft mod, Minecraft 1.21.1 + Fabric instance, and the Python helpers that link Echo and Minecraft. |
| Map patch | Built **on your PC from your own Echo files** (no Echo data is shipped) and added to the copy. It hides the arena and adds the anchor block that EchoCraft's collision is built from. |
| Echo plugins | `EchoCraftClient.dll` (draws Minecraft in Echo) and `EchoCraftPhysics.dll` (Minecraft blocks become Echo collision) go into the copy's `bin\win10\plugins`. |
| Echo API | Turns on Echo's local API (*Settings ▸ Game ▸ API Access*). EchoCraft reads your head and hands from it. The previous settings file is saved as `settings_mp_v2.json.before-echocraft`. |
| Minecraft | Opens Prism so you sign in yourself. Your account stays in Prism; the installer never sees your password. |

## Playing

1. The host starts the EchoCraft servers. Join the Echo server the usual way (Discord / Spark link).
2. Echo starts Minecraft in the background by itself and joins the EchoCraft Minecraft server. Give it a few
   seconds after loading in, and look around while the world locks on.
3. All players share one coordinate mapping, so everyone sees the same Minecraft world in the same place.

## Uninstall

Close Echo, double-click **`Uninstall-EchoCraft.cmd`**. It deletes the EchoCraft copy of Echo and renames
`(original_backup)` back to `ready-at-dawn-echo-arena`. It can also delete `%LOCALAPPDATA%\EchoCraft` (Prism, the
Minecraft downloads and your Prism sign-in).

To **reinstall or update**, run `Install-EchoCraft.cmd` again. It rebuilds the copy from your backup.

## Troubleshooting

| Problem | What to do |
|---|---|
| "Could not rename the Echo folder" | Close the Meta Horizon app, Echo, and any file explorer window inside the Echo folder, then try again. |
| "not the Echo build EchoCraft was made for" | EchoCraft only supports the final Echo VR client. |
| "No plugin loader" | Set up community Echo VR first so you can play online. |
| Minecraft never appears in Echo | Check `%LOCALAPPDATA%\EchoCraft\app\runtime\client-observation\engine-status.json`. Make sure Echo's API Access setting is on. |
| No collision / can't grab blocks | The map patch must be installed: run the installer again. Check `%LOCALAPPDATA%\EchoCraft\physics-world-client.json`. |
| Can't join the Minecraft server | The host's server must be running and reachable on port 25565. |

## For the host (maintainer notes)

- `installer.json` holds the Minecraft server address(es), the shared coordinate mapping (`fixedCoordinates`:
  Minecraft origin + forward, which must match the host's `%LOCALAPPDATA%\EchoCraft\fixed-coordinates.enabled`), and
  the pinned downloads.
- `payload/` holds EchoCraft's own built files: the Echo plugins, the mod jar, the runtime Python tools and the
  map-patch input (geometry only). Rebuild them from the main repository and copy them in when EchoCraft changes.
- Only you, as the host, run the Echo dedicated server and the Minecraft server. Server-side Echo collision currently
  follows the host's own surroundings, so players far from the host may see collision glitches.

## Credits and legal

EchoCraft uses [Vivecraft](https://github.com/Vivecraft/VivecraftMod) (LGPLv3), [Fabric](https://fabricmc.net/),
[Prism Launcher](https://prismlauncher.org/) (GPLv3) and [MinHook](https://github.com/TsudaKageyu/minhook) (BSD-2-Clause,
see `payload/echo-plugins/EchoCraftClient-LICENSE.txt`). Those are downloaded from their official releases, not
redistributed here (MinHook is compiled into the plugin).

Minecraft is a trademark of Mojang/Microsoft. Echo VR is a trademark of Ready At Dawn/Meta. This project is
unaffiliated, ships no Minecraft or Echo game files or assets, and modifies only a copy of your own Echo install.
