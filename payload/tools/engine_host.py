"""Echo-owned hidden Minecraft worker and live VR inventory image transport."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time
from urllib.error import HTTPError, URLError
from bridge_cli import Bridge, DEFAULT_CONFIG, ROOT
from live_world_feed import atomic_json
from native_world_material import read_png
from native_world_layers import png_rgba

OUTPUT = ROOT/'runtime/client-observation'
kernel = ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
kernel.OpenProcess.restype=wintypes.HANDLE
kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
kernel.CreateMutexW.restype=wintypes.HANDLE


def process(pid):
    handle=kernel.OpenProcess(0x1000|0x100000,False,pid)
    if not handle:raise ValueError('Process unavailable')
    text=ctypes.create_unicode_buffer(32768);length=wintypes.DWORD(len(text))
    if not kernel.QueryFullProcessImageNameW(handle,0,text,ctypes.byref(length)):
        kernel.CloseHandle(handle);raise ValueError('Cannot verify process')
    return handle,Path(text.value)


def alive(handle):return kernel.WaitForSingleObject(handle,0)==258


def atomic_bytes(path,data):
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(data)
    # CRT/WIC readers and OneDrive may briefly hold the previous generation
    # without FILE_SHARE_DELETE. Keep the old complete file until replacement.
    for attempt in range(40):
        try:temp.replace(path);return
        except PermissionError:
            if attempt==39:raise
            time.sleep(.025)


def square_panel(png):
    w,h,rgba=read_png(png)
    size=max(w,h)
    if size>2048:raise ValueError('Inventory image exceeds texture budget')
    data=bytearray(size*size*4);left=(size-w)//2;top=(size-h)//2
    for y in range(h):data[((y+top)*size+left)*4:((y+top)*size+left+w)*4]=rgba[y*w*4:(y+1)*w*4]
    return png_rgba(size,size,data)


class InventoryTransport:
    def __init__(self, output=OUTPUT):
        self.output=output;self.serial=0;self.previous_toggle=None

    def close(self):
        atomic_bytes(self.output/'ui-control.bin',struct.pack('<III',0x45435549,0,self.serial))

    def tick(self, bridge, state):
        try:
            magic,toggle=struct.unpack('<II',(self.output/'ui-input.bin').read_bytes())
            if magic!=0x45435549:raise ValueError('Invalid input header')
        except (OSError,ValueError,struct.error):toggle=self.previous_toggle
        changed=self.previous_toggle is not None and toggle is not None and (toggle-self.previous_toggle)%2
        # Consume before sending: a lost action response must never repeat a
        # toggle, which could close a menu that was successfully opened.
        self.previous_toggle=toggle
        if not (self.output/'ui.enabled').exists():
            self.close();return False
        if not state['connected']:
            self.close();return False
        if changed:
            menu=bridge.request('/menu')
            if menu['open']:bridge.request('/ui',dict(type='close_menu',worldSession=state['worldSession'],token=menu['token']))
            elif not state['screenOpen']:
                bridge.request('/ui',dict(type='open_inventory',worldSession=state['worldSession']))
                time.sleep(.2)
        menu=bridge.request('/menu_frame')
        if not menu['open']:
            self.close();return False
        source=DEFAULT_CONFIG.parent.parent/'echocraft-export/menu-frame.png'
        atomic_bytes(self.output/'ui-menu.png',square_panel(source.read_bytes()))
        self.serial+=1
        atomic_bytes(self.output/'ui-control.bin',struct.pack('<III',0x45435549,1,self.serial))
        atomic_json(self.output/'ui-menu.json',menu)
        return True


def bridge_error(error):
    if isinstance(error,HTTPError):
        try:
            message=json.loads(error.read(4096)).get('error','Request failed')
            return f'HTTP {error.code}: {message}'
        except (ValueError,OSError):pass
        finally:error.close()
    return str(error)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--echo-pid',required=True,type=int)
    args=parser.parse_args();handle,exe=process(args.echo_pid)
    expected=Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena\bin\win10\echovr.exe')
    if exe!=expected or hashlib.sha256(exe.read_bytes()).hexdigest()!='3dae0cdab2eb298f9b04fc6baac83f8dd304a8f1d9fea057ab30438fe271df9a':
        kernel.CloseHandle(handle);raise ValueError('Not the verified Echo client')
    mutex=kernel.CreateMutexW(None,False,'Local\\EchoCraftEngineHost')
    if not mutex:raise ValueError('Cannot create engine lock')
    if ctypes.get_last_error()==183:kernel.CloseHandle(mutex);kernel.CloseHandle(handle);return
    OUTPUT.mkdir(parents=True,exist_ok=True)
    bridge=None;engine_pid=None;hidden=False;transport=InventoryTransport();failed_since=None;feeds=[]
    try:
        try:bridge=Bridge();state=bridge.request('/state')
        except Exception:
            # A live discovered JVM may still be loading; never launch a duplicate.
            running=False
            try:
                discovered=json.loads(DEFAULT_CONFIG.read_text());worker,path=process(discovered['pid'])
                running=alive(worker) and path.name.lower() in ('java.exe','javaw.exe');kernel.CloseHandle(worker)
            except Exception:pass
            if not running:
                startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
                subprocess.Popen([str(ROOT/'client/PrismLauncher/prismlauncher.exe'),'--launch','EchoCraft'],
                                 startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+120
            while alive(handle) and time.monotonic()<deadline:
                try:bridge=Bridge();state=bridge.request('/state');break
                except Exception:time.sleep(.5)
            else:raise RuntimeError('Minecraft engine did not become available')
        if 'engine' not in state:raise RuntimeError('Restart Minecraft with the background-engine mod build')
        engine_pid=json.loads(DEFAULT_CONFIG.read_text())['pid']
        bridge.request('/engine',dict(type='hide'));hidden=True
        if (OUTPUT/'live-world.enabled').exists():
            import live_feeds
            feeds=live_feeds.start_all(OUTPUT)
        while alive(handle):
            try:
                state=bridge.request('/state')
                vivecraft=state.get('vivecraft',{}).get('integration',{})
                native_gui=vivecraft.get('mode')=='echo-ui'
                menu_open=bool(state.get('screenOpen')) if native_gui else transport.tick(bridge,state)
                status=dict(echoPid=args.echo_pid,enginePid=engine_pid,connected=state['connected'],
                            engine=state['engine'],menuOpen=menu_open,inventoryImageSerial=transport.serial,
                            headsetVisibility='unverified',handPointerConnected=native_gui,
                            interface='vivecraft' if native_gui else 'legacy-disabled',vivecraft=vivecraft,
                            liveFeeds={feed.name:feed.report() for feed in feeds})
                atomic_json(OUTPUT/'engine-status.json',status)
                failed_since=None
            except (HTTPError,URLError,TimeoutError,PermissionError) as error:
                if isinstance(error,HTTPError) and error.code not in (400,503):raise
                message=bridge_error(error)
                if failed_since is None:failed_since=time.monotonic()
                if time.monotonic()-failed_since>=10:raise RuntimeError(message) from error
                # Native freshness also hides images if a reader blocks close.
                try:transport.close()
                except PermissionError:pass
                atomic_json(OUTPUT/'engine-status.json',dict(connected=False,recovering=True,
                            echoPid=args.echo_pid,error=message))
            time.sleep(.2)
    except Exception as error:
        atomic_json(OUTPUT/'engine-status.json',dict(error=bridge_error(error),connected=False,echoPid=args.echo_pid))
        # If the transport fails, restore access to the world instead of leaving
        # a hidden, unreachable game. A normal Echo exit saves and stops below.
        if hidden and alive(handle) and bridge:
            try:bridge.request('/engine',dict(type='show'))
            except Exception:pass
        raise
    finally:
        for feed in feeds:feed.stop.set()
        try:transport.close()
        except OSError:pass # Stale controls expire; still save the world below.
        if hidden and not alive(handle) and bridge:
            try:
                if json.loads(DEFAULT_CONFIG.read_text())['pid']==engine_pid:
                    bridge.request('/engine',dict(type='save_and_stop'))
            except Exception:pass
        kernel.CloseHandle(mutex);kernel.CloseHandle(handle)


if __name__=='__main__':main()
